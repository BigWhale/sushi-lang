"""The symbol of a unit-level global, a constant or a `var`, has one derivation (docs/design/ir.md 8.2).

The declaration table names each global with `global_symbol` for the back end (ir.md 6.3),
and the typecheck pass writes the symbol of the global of a `var` that a `StorageRef` names
as a row, which SLIR lowering reads for a `Global` place. SLIR lowering makes no function of
an initializer body, so these two are the callers. The symbols are the ones that the back
end links today. The tests call the function over data and scan the Python source; they do not run
the compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.ast import ConstDef, IntLit, VarDef
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.unit_symbols import global_symbol

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
I32 = BuiltinType.I32


def _const(**fields: object) -> ConstDef:
    return ConstDef(None, name="STEP", ty=I32, value=IntLit(None, 3), **fields)  # type: ignore[arg-type]


def _var(**fields: object) -> VarDef:
    return VarDef(None, name="count", ty=I32, value=IntLit(None, 0), **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(("decl", "unit", "symbol"), [
    (_const(), "test_main", "test_main$STEP"),
    (_const(), "units/levels", "units$levels$STEP"),
    (_const(home_unit="lib/geo/shapes"), "test_main", "lib$geo$shapes$STEP"),
    (_const(scope_unit="lib/geo/shapes"), "test_main", "lib$geo$shapes$STEP"),
    (_var(), "test_main", "test_main$count"),
    (_var(), "units/levels", "units$levels$count"),
    (_var(scope_unit="lib/geo/shapes"), "test_main", "lib$geo$shapes$count"),
    (_var(home_unit="lib/geo/shapes"), "test_main", "test_main$count"),
    (_var(link_symbol="geo$count"), "test_main", "geo$count"),
], ids=["const in the program unit", "const in another unit", "const of a library copy",
        "const of a compiled library", "var in the program unit", "var in another unit",
        "var of a compiled library", "var ignores the home unit", "var of a binary library"])
def test_a_global_has_one_symbol(decl: ConstDef, unit: str, symbol: str) -> None:
    assert global_symbol(decl, unit) == symbol


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


def test_the_declaration_table_and_the_typecheck_arm_of_a_var_call_it() -> None:
    assert sorted(set(_callers("global_symbol"))) == [
        "semantics/decl_table.py", "semantics/passes/types/visit/expressions.py"]


def test_slir_lowering_names_no_constant_declaration() -> None:
    """The global of a constant or a `var` takes the value of the constant evaluator (ir.md
    8.2), so no module of SLIR lowering reads a `ConstDef`."""
    names = set()
    for path in sorted((ROOT / "semantics" / "slir").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names.update(str(path.relative_to(ROOT)) for node in ast.walk(tree)
                     if isinstance(node, (ast.Name, ast.alias))
                     and getattr(node, "id", getattr(node, "name", None)) in ("ConstDef", "VarDef"))
    assert names == set()
