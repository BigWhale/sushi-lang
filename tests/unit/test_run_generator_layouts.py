"""The `run()` generator of `<sys/process>` reads the `T[]` layout through `gep_utils`, and
it checks every `malloc` (#896).

The descriptor-GEP gate compiles a probe program, and a stdlib generator runs only when
the stdlib is built, so that gate does not see this generator. This gate calls the
generator directly. A failed `malloc` raises RE2021, the rule of the `<io/files>`
generators.
"""
from __future__ import annotations

import sys
from pathlib import Path

from llvmlite import ir

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
GEP_UTILS = "backend/gep_utils.py"
TRIO = {"gep_dynamic_array_len", "gep_dynamic_array_cap", "gep_dynamic_array_data"}


def _module_of(frame) -> str:
    path = Path(frame.f_code.co_filename).resolve()
    try:
        return path.relative_to(SOURCE_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_descriptor_field_gep(ptr, indices) -> bool:
    pointee = getattr(ptr.type, "pointee", None)
    if not isinstance(pointee, ir.LiteralStructType) or len(pointee.elements) != 3:
        return False
    first, second, third = pointee.elements
    if first != ir.IntType(32) or second != ir.IntType(32) or not isinstance(third, ir.PointerType):
        return False
    if len(indices) != 2 or not all(isinstance(i, ir.Constant) for i in indices):
        return False
    return indices[0].constant == 0 and indices[1].constant in (0, 1, 2)


def _generate_run(monkeypatch=None, record=None) -> ir.Function:
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
    from sushi_lang.sushi_stdlib.src.sys.process.functions import generate_run

    if monkeypatch is not None:
        original = ir.IRBuilder.gep

        def recording_gep(self, ptr, indices, *args, **kwargs):
            if _is_descriptor_field_gep(ptr, indices):
                frame = sys._getframe(1)
                via = [f.f_code.co_name for f in _frames(frame) if _module_of(f) == GEP_UTILS]
                record.append(bool(via) and via[-1] in TRIO)
            return original(self, ptr, indices, *args, **kwargs)

        monkeypatch.setattr(ir.IRBuilder, "gep", recording_gep)

    module = create_stdlib_module("sys.process")
    generate_run(module)
    return module.get_global("sushi_run")


def _frames(frame):
    # Only the frames between the generator and the GEP: stop at the generator.
    while frame is not None:
        yield frame
        if frame.f_code.co_name == "generate_run":
            return
        frame = frame.f_back


def test_the_args_descriptor_is_read_through_gep_utils(monkeypatch):
    record: list[bool] = []
    _generate_run(monkeypatch, record)
    assert record, "control: the generator made no descriptor-shaped GEP"
    assert all(record), (
        f"{record.count(False)} of {len(record)} `T[]` descriptor field GEPs in generate_run "
        "do not go through the gep_utils trio")


#: The mallocs `generate_run` does not make itself, by the name of their value: the shared
#: `fat_pointer_to_cstr` helper makes them, and this gate does not judge it.
HELPER_MALLOCS = {"cstr"}


def _is_malloc_call(instr) -> bool:
    return (isinstance(instr, ir.CallInstr) and getattr(instr.callee, "name", "") == "malloc"
            and instr.name.rstrip(".0123456789") not in HELPER_MALLOCS)


def _calls_exit(block: ir.Block) -> bool:
    return any(isinstance(i, ir.CallInstr) and getattr(i.callee, "name", "") == "exit"
               for i in block.instructions)


def test_every_malloc_in_run_is_checked_for_null():
    func = _generate_run()
    instructions = [i for block in func.blocks for i in block.instructions]
    mallocs = [i for i in instructions if _is_malloc_call(i)]
    assert len(mallocs) == 3, f"control: expected the argv and two capture buffers, got {len(mallocs)}"

    unchecked = []
    for call in mallocs:
        compares = [i for i in instructions if isinstance(i, ir.ICMPInstr) and i.op == "eq"
                    and call in i.operands and any(
                        isinstance(o, ir.Constant) and o.constant is None for o in i.operands)]
        branches = [i for i in instructions if isinstance(i, ir.ConditionalBranch)
                    and any(c in i.operands for c in compares)]
        if not any(_calls_exit(br.operands[1]) for br in branches):
            unchecked.append(str(call).strip())
    assert not unchecked, "a malloc in sushi_run is used with no RE2021 guard:\n  " + "\n  ".join(unchecked)
