"""A call of a refused extension method adds no diagnostic of its own (#808).

A refused declaration is not filed in the extension table, so every call of it read
as an undefined name, CE2008, once per call site. The user wrote one fault. The
collect pass records the refused `(target base, method)` pair next to the refusal, and
the method-call path reads the record before it reports CE2008.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is absent, so the
code LIST is asserted here, on the analyze path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "extensions" / "refused_target_call"

_CASES = {
    "test_err_refused_count_target_call.sushi": ["CE2062"],
    "test_err_refused_mixed_target_call.sushi": ["CE2098"],
    "test_err_never_declared_method_call.sushi": ["CE2062", "CE2008"],
}


def _codes(analyze, src: str) -> list[str]:
    return [item.code for item in analyze(src, name="rc").items
            if item.code.startswith("CE")]


@pytest.mark.parametrize("fixture", list(_CASES))
def test_a_refused_declaration_is_one_diagnostic(analyze, fixture):
    assert _codes(analyze, (_DIR / fixture).read_text()) == _CASES[fixture]


_TEMPLATE_SHADOW = """struct Box@(T):
    T item

extend Box@(T) pick@(T)() i32:
    return 1

fn main() i32:
    let Box@(i32) b = Box(1)
    println("{b.pick()}")
    return Result.Ok(0)
"""


def test_every_refusal_that_drops_the_method_is_recorded(analyze):
    """CE2064 drops the method too, so its calls are silent as well."""
    assert _codes(analyze, _TEMPLATE_SHADOW) == ["CE2064"]


def test_control_an_undeclared_method_is_still_ce2008(analyze):
    src = ("struct Box@(T):\n    T item\n\n"
           "fn main() i32:\n    let Box@(i32) b = Box(1)\n"
           "    println(\"{b.nothing()}\")\n    return Result.Ok(0)\n")
    assert _codes(analyze, src) == ["CE2008"]
