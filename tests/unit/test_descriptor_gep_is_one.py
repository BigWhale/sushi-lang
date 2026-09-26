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
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

RETIRED = {"get_dynamic_array_len_ptr", "get_dynamic_array_cap_ptr", "get_dynamic_array_data_ptr"}


#: Modules whose `{i32, i32, T*}` GEPs index another struct of that shape, and which make
#: no descriptor GEP of their own.

#: Functions in a module that ALSO makes descriptor GEPs: only these functions may index a
#: struct of the same shape.







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


