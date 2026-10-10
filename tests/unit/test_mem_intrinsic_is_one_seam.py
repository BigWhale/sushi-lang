"""The `llvm.mem*` intrinsics have one home: `backend/expressions/memory.py` (#874).

`emit_memcpy_bytes`, `emit_memmove_bytes` and `emit_memset_bytes` are the entries. Each one declares the
i64-length form and zero-extends the byte count, so a caller cannot pass a raw i32 length
(#149, #151, #152). A second declaration beside them is a copy that can lose that rule,
so a `declare_intrinsic('llvm.mem...')` anywhere else under `sushi_lang/backend/` is
refused, and every declaration under `sushi_lang/` has an i64 length.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"
BACKEND = PACKAGE / "backend"

SEAM_MODULE = "backend/expressions/memory.py"

# An i32 length is a copy of the old form; `i64`, `ir.IntType(64)` and
# `ir.IntType(INT64_BIT_WIDTH)` are the i64 spellings.
_I32_LENGTH = re.compile(r"\bi32\b|IntType\(32\)|INT32_BIT_WIDTH")


def _mem_intrinsic_declarations(source: str) -> list[ast.Call]:
    """Every `declare_intrinsic` call that names an `llvm.mem*` intrinsic, or a name
    the scan cannot read."""
    calls = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "declare_intrinsic" and node.args):
            continue
        name = node.args[0]
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)) \
                or name.value.startswith("llvm.mem"):
            calls.append(node)
    return sorted(calls, key=lambda call: call.lineno)


def _every_declaration() -> list[tuple[str, ast.Call]]:
    """`(path under sushi_lang/, call)` for every declaration in the package."""
    return [(path.relative_to(PACKAGE).as_posix(), call)
            for path in sorted(PACKAGE.rglob("*.py"))
            for call in _mem_intrinsic_declarations(path.read_text(encoding="utf-8"))]


def _i32_length(call: ast.Call) -> str | None:
    """The length type of a declaration when it is an i32, else None."""
    if len(call.args) < 2 or not isinstance(call.args[1], ast.List) or not call.args[1].elts:
        return None
    length = ast.unparse(call.args[1].elts[-1])
    return length if _I32_LENGTH.search(length) else None


def test_no_mem_intrinsic_declared_outside_the_seam():
    found = [f"{rel}:{call.lineno}" for rel, call in _every_declaration()
             if rel.startswith("backend/") and rel != SEAM_MODULE]
    assert not found, (
        "declare llvm.mem* only in backend/expressions/memory.py; call "
        "emit_memcpy_bytes / emit_memmove_bytes / emit_memset_bytes:\n" + "\n".join(found))


def test_mem_intrinsics_declare_i64_length():
    found = [f"{rel}:{call.lineno}: length {_i32_length(call)!r}"
             for rel, call in _every_declaration() if _i32_length(call)]
    assert not found, (
        "llvm.mem* intrinsics must use an i64 (size_t) length; zero-extend the i32 "
        "size at the call site (issues #149/#151, docs/design/string-representation.md):\n"
        + "\n".join(found))


def test_the_seam_declares_the_three_intrinsics():
    source = (PACKAGE / SEAM_MODULE).read_text()
    assert "'llvm.memcpy'" in source and "'llvm.memmove'" in source
    assert "'llvm.memset'" in source
    assert len(_mem_intrinsic_declarations(source)) == 3


def test_the_gate_sees_a_declaration():
    source = '''
def shift(codegen, i8p, i64, i32):
    fn = codegen.module.declare_intrinsic(
        'llvm.memmove', [i8p, i8p, i64])
    other = codegen.module.declare_intrinsic(intrinsic_name, [i8p])
    codegen.module.declare_intrinsic('llvm.ctpop', [i64])
    codegen.module.declare_intrinsic('llvm.memset', [i8p, ir.IntType(8), i32])
'''
    calls = _mem_intrinsic_declarations(source)
    assert [call.lineno for call in calls] == [3, 5, 7]
    assert [_i32_length(call) for call in calls] == [None, None, "i32"]
