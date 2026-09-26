"""A perk implementation on one instantiation is judged where it is written (#898).

The header -- the contract check and the CE4007 name check -- has one answer with an
instance of the target and without one, the same as for a template target.
`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is alone, so the code
SET is asserted here, on the analyze path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[1]
_DIR = "perks/unreached_concrete_impl_header"

_CASES = {
    f"{_DIR}/test_err_perk_concrete_conflict_unreached.sushi": {"CE4007"},
    f"{_DIR}/test_err_perk_concrete_conflict_reached.sushi": {"CE4007"},
    f"{_DIR}/test_err_perk_concrete_conflict_template_unreached.sushi": {"CE4007"},
    f"{_DIR}/test_err_perk_concrete_contract_unreached.sushi": {"CE4004"},
    f"{_DIR}/test_perk_concrete_impl_unreached_clean.sushi": set(),
}


def _codes(analyze, src: str) -> list[str]:
    return sorted(item.code for item in analyze(src, name="pk").items
                  if item.code.startswith("CE"))


@pytest.mark.parametrize("fixture", list(_CASES))
def test_the_header_has_one_answer(analyze, fixture):
    assert _codes(analyze, (_TESTS / fixture).read_text()) == sorted(_CASES[fixture])


@pytest.mark.parametrize("fixture", [f for f in _CASES if "unreached" in Path(f).name])
def test_the_answer_does_not_depend_on_an_instance(analyze, fixture):
    """The same source with an instance of `Box@(i32)` gives the same codes."""
    src = (_TESTS / fixture).read_text()
    reached = (src.replace('let Box@(string) b = Box("x")', "let Box@(i32) b = Box(1)")
               .replace("println(b.v)", 'println("{b.v}")'))
    assert reached != src
    assert _codes(analyze, reached) == _codes(analyze, src)
