"""`--lib-info` writes the target of an extension one way in every section (#1070).

The "Extension Methods" section and the "Foreign Extensions" section both name the
target of an extension. A bound that the target writes is part of the target, so the
two sections read one formatter, `_render_extension_target`, and a foreign record
carries the bounds as an extension record does. A pure formatter test over manifest
records: no Sushi source is parsed and the compiler does not run.
"""
from __future__ import annotations

from sushi_lang.compiler.lib_info import _extension_line, _foreign_line
from sushi_lang.internals.styling import Palette

BOUNDS = [{"param": "T", "perks": ["Clone"]}]


def _palette() -> Palette:
    return Palette(False)


def test_a_foreign_array_target_prints_its_bound():
    claim = {"type": "T[]", "method": "head", "target_bounds": BOUNDS}
    assert _foreign_line(claim, _palette()) == "  extend (T: Clone)[] head"


def test_a_foreign_generic_target_prints_its_bound():
    claim = {"type": "List@(T)", "method": "dup", "target_bounds": BOUNDS}
    assert _foreign_line(claim, _palette()) == "  extend List@(T: Clone) dup"


def test_a_foreign_target_with_no_bound_prints_as_written():
    claim = {"type": "List@(T)", "method": "size"}
    assert _foreign_line(claim, _palette()) == "  extend List@(T) size"


def test_the_two_sections_print_one_target():
    ext = {"type": "T[]", "name": "head", "params": [], "return_type": "i32",
           "target_bounds": BOUNDS}
    claim = {"type": "T[]", "method": "head", "target_bounds": BOUNDS}
    target = "(T: Clone)[]"
    assert _extension_line(ext, _palette()).startswith(f"  extend {target} head(")
    assert _foreign_line(claim, _palette()) == f"  extend {target} head"
