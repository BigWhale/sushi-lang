"""The value that an integer literal holds is decided one time, in `typecheck` (docs/design/ir.md 8.5, ruling R-W).

At three positions a literal holds the value that its type holds, through the one integer
kernel: the operand of a cast, the operand of a negation, and a literal with a radix other
than 10. At every other position the value is carried unchanged. `held_value` is the one
function that decides it, and the one writer of a type row (`visit.answer`) is its one
caller, so the value row and the type row of a literal are written together. These tests
call a Python function over data and scan the Python source; they do not run the compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.passes.types.visit.expressions import held_value
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import BuiltinType

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

I8 = BuiltinType.I8
U8 = BuiltinType.U8
I32 = BuiltinType.I32


@pytest.mark.parametrize(("const", "ty", "wraps", "held"), [
    (e.IntConst(300), U8, True, 44),
    (e.IntConst(128), I8, True, -128),
    (e.IntConst(0xFF, radix=16), I8, False, -1),
    (e.IntConst(0b1000_0000, radix=2), I8, False, -128),
    (e.IntConst(200), U8, False, 200),
    (e.IntConst(42), I32, True, 42),
    (e.IntConst(120, byte_spelling="a'x'"), U8, False, 120),
], ids=["cast", "negation", "hexadecimal", "binary", "decimal", "fits", "byte"])
def test_a_literal_holds_the_value_of_its_position(const: e.IntConst, ty: BuiltinType,
                                                    wraps: bool, held: int) -> None:
    assert held_value(const, ty, wraps) == held


def _callers(name: str) -> list[str]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called == name:
                    found.append(str(path.relative_to(ROOT)))
    return found


def test_the_writer_of_a_type_row_is_the_one_caller() -> None:
    assert _callers("held_value") == ["semantics/passes/types/visit/__init__.py"]


def test_slir_lowering_decides_no_held_value() -> None:
    """SLIR lowering reads the value row; it never calls the integer kernel (ruling R-W)."""
    slir = ROOT / "semantics" / "slir"
    importers = [path.name for path in sorted(slir.glob("*.py"))
                 if "integer_ops" in path.read_text(encoding="utf-8")]
    assert importers == []
