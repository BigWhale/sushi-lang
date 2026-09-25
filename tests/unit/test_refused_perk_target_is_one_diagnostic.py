"""A refused generic perk-implementation target is one diagnostic (#860).

The collect pass refuses a wrong type-argument count (CE2062) and a target that mixes
concrete arguments with type parameters (CE2098). The refusal is recorded by the base
name and each method name the implementation declares, beside the extension path's
record, so a call of one of those methods adds no CE2008. A method the implementation
never declared is still an undefined name.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is absent, so the code
LIST is asserted here, on the analyze path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[1]

_CASES = {
    "perks/refused_target/test_err_perk_target_count_refused.sushi": ["CE2062"],
    "perks/refused_target/test_err_perk_target_mixed_refused.sushi": ["CE2098"],
    "perks/refused_target/test_err_perk_target_refused_other_method.sushi":
        ["CE2062", "CE2008"],
}


def _codes(analyze, src: str) -> list[str]:
    return [item.code for item in analyze(src, name="prt").items
            if item.code.startswith("CE")]


@pytest.mark.parametrize("fixture", list(_CASES))
def test_one_fault_is_one_diagnostic(analyze, fixture):
    assert _codes(analyze, (_TESTS / fixture).read_text()) == _CASES[fixture]


def test_one_helper_emits_ce2098():
    """The extension collector and the perk collector share one CE2098 emit."""
    root = Path(__file__).resolve().parents[2] / "sushi_lang"
    sites = [p.relative_to(root).as_posix()
             for p in root.rglob("*.py") if "ERR.CE2098" in p.read_text()]
    assert sites == ["semantics/generics/extension_targets.py"]
