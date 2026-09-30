"""One equality, one order and one string form for a held value.

`backend/types/contracts.py` holds the equality and the order, and
`backend/types/display.py` the string form (`Display`).

`==` on a struct, `contains`, the HashMap probe and a field of a derived equality all
compare through `emit_value_eq`; the order operators and `compare` through
`emit_value_compare`. The HashMap probe had a structural equality of its own
(`emit_struct_equality`, `emit_enum_equality`, two array walks), which could not see an
`Eq` implementation. Those names must not come back, and no other module may define a
function that compares two composite values.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
HOME = ROOT / "backend" / "types" / "contracts.py"
RETIRED = {
    "emit_key_equality_check", "emit_struct_equality", "emit_enum_equality",
    "emit_fixed_array_equality", "emit_dynamic_array_equality",
    "_emit_elementwise_equality",
}
SEAMS = {"emit_value_eq", "emit_value_compare"}
DISPLAY_HOME = ROOT / "backend" / "types" / "display.py"
DISPLAY_SEAMS = {"emit_value_to_str", "emit_value_fmt"}


def _defined(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_retired_equalities_stay_retired():
    hits = [f"{p.relative_to(ROOT)}: {sorted(_defined(p) & RETIRED)}"
            for p in sorted(ROOT.rglob("*.py")) if _defined(p) & RETIRED]
    assert hits == []


def test_the_seams_are_defined_once():
    homes = [p.relative_to(ROOT) for p in sorted(ROOT.rglob("*.py")) if _defined(p) & SEAMS]
    assert homes == [HOME.relative_to(ROOT)]


def test_the_control_finds_the_seams():
    assert SEAMS <= _defined(HOME)


def test_the_display_seams_are_defined_once():
    homes = [p.relative_to(ROOT) for p in sorted(ROOT.rglob("*.py"))
             if _defined(p) & DISPLAY_SEAMS]
    assert homes == [DISPLAY_HOME.relative_to(ROOT)]
    assert DISPLAY_SEAMS <= _defined(DISPLAY_HOME)
