"""`--lib-info` writes the target of an extension one way, with its marker (#1070, #1251).

The "Extension Methods" section names the target of an extension. A bound that the
target writes is part of the target, so the section reads one formatter,
`_render_extension_target`. The "Foreign Extensions" section was retired with CW3003
(`docs/design/extension-visibility.md` section 6), and the section now prints the
`public` marker of each record. A pure formatter test over manifest records: no Sushi
source is parsed and the compiler does not run.
"""
from __future__ import annotations

from sushi_lang.compiler.lib_info import _extension_line
from sushi_lang.internals.styling import Palette

BOUNDS = [{"param": "T", "perks": ["Clone"]}]


def _palette() -> Palette:
    return Palette(False)


def _record(target: str, **extra) -> dict:
    return {"type": target, "name": "head", "params": [], "return_type": "i32", **extra}


def test_an_array_target_prints_its_bound():
    line = _extension_line(_record("T[]", target_bounds=BOUNDS), _palette())
    assert line.startswith("  extend (T: Clone)[] head(")


def test_a_generic_target_prints_its_bound():
    line = _extension_line(_record("List@(T)", target_bounds=BOUNDS), _palette())
    assert line.startswith("  extend List@(T: Clone) head(")


def test_a_target_with_no_bound_prints_as_written():
    assert _extension_line(_record("List@(T)"), _palette()).startswith(
        "  extend List@(T) head(")


def test_a_public_extension_prints_its_marker():
    line = _extension_line(_record("i32", public=True), _palette())
    assert line == "  public extend i32 head() i32"


def test_a_private_extension_prints_no_marker():
    line = _extension_line(_record("i32", public=False), _palette())
    assert line == "  extend i32 head() i32"


def test_a_public_static_prints_both_words():
    line = _extension_line(_record("Vec", public=True, static=True), _palette())
    assert line == "  public extend Vec static head() i32"
