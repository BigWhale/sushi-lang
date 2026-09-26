"""Only `backend/gep_utils.py` computes the address of a `T[]` descriptor field (#878).

A `T[]` descriptor is `{i32 len, i32 cap, T* data}`. Three spellings used to compute the
address of one of its fields: the `LLVMTypeSystem.get_dynamic_array_*_ptr` trio, the
`gep_utils.gep_dynamic_array_*` trio, and a raw GEP with a literal 0, 1 or 2. Now the
`gep_utils` trio is the one spelling.

Two gates. The SOURCE gate refuses the retired trio by name. The EMITTED gate reads the
GEPs the compiler makes: the source alone cannot tell a descriptor GEP from another
struct GEP, and the IR type alone cannot either, because a `List@(T)`, an iterator slot
and the HashMap buckets field have the same LLVM type `{i32, i32, T*}`. So the gate
compiles one probe program in-process, records every GEP `[0, k]` (k = 0, 1 or 2) on a
pointer to `{i32, i32, T*}`, and accepts it only when the `gep_utils` trio made it, or
when the Python function that made it is on the SAME_SHAPE list, which names the struct
it indexes. A new raw descriptor GEP in a function that is not on the list fails here.
The gate sees only what the probe reaches. A stdlib generator runs in the probe only
when the stdlib bitcode is stale (the compile rebuilds it in process), so a generator
must follow the rule too, or the gate fails on a stale tree and passes on a fresh one.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from llvmlite import ir

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

RETIRED = {"get_dynamic_array_len_ptr", "get_dynamic_array_cap_ptr", "get_dynamic_array_data_ptr"}

GEP_UTILS = "backend/gep_utils.py"
TRIO = {"gep_dynamic_array_len", "gep_dynamic_array_cap", "gep_dynamic_array_data"}

#: Modules whose `{i32, i32, T*}` GEPs index another struct of that shape, and which make
#: no descriptor GEP of their own.
SAME_SHAPE_MODULES = {
    "backend/generics/list/types.py": "a List@(T): {len, cap, data}",
    "backend/generics/list/methods_iter.py": "an iterator slot",
    "backend/generics/hashmap/types.py": "the HashMap buckets field (the HashMap's own layout)",
    "backend/generics/hashmap/methods/core.py": "the HashMap buckets field",
    "backend/generics/hashmap/methods/iterators.py": "an iterator slot, the HashMap buckets field",
    "backend/statements/loops.py": "an iterator slot",
}

#: Functions in a module that ALSO makes descriptor GEPs: only these functions may index a
#: struct of the same shape.
SAME_SHAPE_FUNCTIONS = {
    ("backend/destructors.py", "_emit_list_value_destructor"): "a List@(T)",
    ("backend/types/arrays/methods/iterators.py", "emit_dynamic_array_iter"): "an iterator slot",
    ("backend/types/arrays/methods/iterators.py", "emit_fixed_array_iter"): "an iterator slot",
}

PROBE = """\
use <collections/hashmap>

fn main(string[] args) i32:
    let i32[] a = from([1, 2, 3])
    a.push(4)
    let i32 n = a.len() + a.capacity()
    let i32 last = a.pop().realise(0)
    let i32[] b = from([5, 6])
    a.extend(b)
    a.truncate(2)
    let u64 h = a.hash()
    foreach(x in a.iter()):
        n := n + x
    let i32[4] fixed = [1, 2, 3, 4]
    foreach(y in fixed.iter()):
        n := n + y
    let u8[] bytes = from([104 as u8, 105 as u8])
    let string s = bytes.to_string()
    let string t = bytes.to_string_checked().realise("")
    let i32[] c = a
    let List@(i32) l = List.new()
    l.push(n)
    foreach(z in l.iter()):
        n := n + z
    let HashMap@(i32, i32) m = HashMap.new()
    m.insert(1, 2)
    foreach(k in m.keys()):
        n := n + k
    println("{n} {last} {h} {s} {t} {c.len()} {args.len()}")
    let i32[] d = from([7])
    d := c
    b.free()
    m.free()
    return Result.Ok(0)
"""


def _module_of(frame) -> str:
    path = Path(frame.f_code.co_filename).resolve()
    try:
        return path.relative_to(SOURCE_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_descriptor_shaped_field_gep(ptr, indices) -> bool:
    pointee = getattr(ptr.type, "pointee", None)
    if not isinstance(pointee, ir.LiteralStructType) or len(pointee.elements) != 3:
        return False
    first, second, third = pointee.elements
    if first != ir.IntType(32) or second != ir.IntType(32) or not isinstance(third, ir.PointerType):
        return False
    if len(indices) != 2 or not all(isinstance(i, ir.Constant) for i in indices):
        return False
    return indices[0].constant == 0 and indices[1].constant in (0, 1, 2)


def test_the_retired_trio_is_gone():
    """`LLVMTypeSystem.get_dynamic_array_*_ptr` is not defined and not called."""
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = (node.attr if isinstance(node, ast.Attribute)
                    else node.id if isinstance(node, ast.Name)
                    else node.name if isinstance(node, ast.FunctionDef) else None)
            if name in RETIRED:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{node.lineno} {name}")
    assert not offenders, (
        "the retired descriptor-field helpers are back:\n  " + "\n  ".join(offenders)
        + "\nUse gep_utils.gep_dynamic_array_len / _cap / _data."
    )


def test_every_descriptor_field_gep_goes_through_gep_utils(tmp_path, monkeypatch):
    """Every `{i32, i32, T*}` field GEP the probe emits is the trio's or a named other struct."""
    from sushi_lang.compiler import cli

    seen_trio: set[str] = set()
    seen_same_shape: set[str] = set()
    offenders: set[str] = set()
    original = ir.IRBuilder.gep

    def recording_gep(self, ptr, indices, *args, **kwargs):
        if _is_descriptor_shaped_field_gep(ptr, indices):
            frame = sys._getframe(1)
            via = None
            while frame is not None and _module_of(frame) == GEP_UTILS:
                via = frame.f_code.co_name
                frame = frame.f_back
            if via in TRIO:
                seen_trio.add(via)
            elif frame is not None:
                module, function = _module_of(frame), frame.f_code.co_name
                site = f"{module}:{frame.f_lineno} in {function}"
                if module in SAME_SHAPE_MODULES or (module, function) in SAME_SHAPE_FUNCTIONS:
                    seen_same_shape.add(site)
                else:
                    offenders.add(site)
        return original(self, ptr, indices, *args, **kwargs)

    monkeypatch.setattr(ir.IRBuilder, "gep", recording_gep)

    src = tmp_path / "probe.sushi"
    src.write_text(PROBE, encoding="utf-8")
    rc = cli.main([str(src), "-o", str(tmp_path / "probe"),
                   "--cache-dir", str(tmp_path / "cache"), "--no-incremental"])
    assert rc == 0, "the probe program does not compile"

    assert not offenders, (
        "a `T[]` descriptor field address is computed outside backend/gep_utils.py:\n  "
        + "\n  ".join(sorted(offenders))
        + "\nUse gep_utils.gep_dynamic_array_len / _cap / _data. If the GEP indexes another "
        "struct of the same shape (a List@(T), an iterator slot), add its function to "
        "SAME_SHAPE_FUNCTIONS and name the struct."
    )
    # A zero is trusted only when the probe reached every helper and a same-shape struct.
    assert seen_trio == TRIO, f"control: the probe reached only {sorted(seen_trio)} of the trio"
    assert seen_same_shape, "control: the probe made no List, iterator or HashMap GEP"
