"""The `llvm.mem*` intrinsics have one home: `backend/expressions/memory.py` (#874).

`emit_memcpy_bytes` and `emit_memmove_bytes` are the entries. Each one declares the
i64-length form and zero-extends the byte count, so a caller cannot pass a raw i32 length
(#149). A second declaration beside them is a copy that can lose that rule, so a
`declare_intrinsic('llvm.mem...')` anywhere else under `sushi_lang/backend/` is refused.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

SEAM_MODULE = "expressions/memory.py"


def _mem_intrinsic_declarations(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "declare_intrinsic" and node.args):
            continue
        name = node.args[0]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            if name.value.startswith("llvm.mem"):
                lines.append(node.lineno)
        else:
            lines.append(node.lineno)
    return lines


def _declarations_outside_the_seam() -> list[str]:
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel == SEAM_MODULE:
            continue
        found.extend(f"{rel}:{line}" for line in _mem_intrinsic_declarations(path.read_text()))
    return found


def test_no_mem_intrinsic_declared_outside_the_seam():
    found = _declarations_outside_the_seam()
    assert not found, (
        "declare llvm.mem* only in backend/expressions/memory.py; call "
        "emit_memcpy_bytes / emit_memmove_bytes:\n" + "\n".join(found))


def test_the_seam_declares_both_intrinsics():
    source = (BACKEND / SEAM_MODULE).read_text()
    assert "'llvm.memcpy'" in source and "'llvm.memmove'" in source
    assert len(_mem_intrinsic_declarations(source)) == 2


def test_the_gate_sees_a_declaration():
    source = '''
def shift(codegen, i8p, i64):
    fn = codegen.module.declare_intrinsic(
        'llvm.memmove', [i8p, i8p, i64])
    other = codegen.module.declare_intrinsic(intrinsic_name, [i8p])
    codegen.module.declare_intrinsic('llvm.ctpop', [i64])
'''
    assert _mem_intrinsic_declarations(source) == [3, 5]
