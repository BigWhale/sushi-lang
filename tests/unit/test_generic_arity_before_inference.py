"""A wrong argument count on a generic call is ONE diagnostic, CE2009 (#790).

The count is checked before inference: a miscount used to answer CE2060, a sentence
about inference, where the explicit spelling of the same call answered CE2009. A
fixture's `EXPECT_ERROR_CODE` is a substring test and cannot prove CE2060 is absent,
so the code set is read here on the analyze path.
"""
from __future__ import annotations

import pytest


def _codes(reporter) -> list[str]:
    """The error codes; an unused-parameter warning is not the question here."""
    return [item.code for item in reporter.items if item.code.startswith("CE")]


PAIR = """\
fn pair@(T)(T a, T b) i32:
    return Result.Ok(0)

fn main() i32:
    let i32 n = pair({args}).realise(0)
    return Result.Ok(n)
"""

LEAD = """\
fn lead@(H, ...Ts)(H a, H b, ...Ts rest) i32:
    return Result.Ok(0)

fn main() i32:
    let i32 n = lead({args}).realise(0)
    return Result.Ok(n)
"""


@pytest.mark.parametrize("source, args", [
    (PAIR, "1"),
    (PAIR, "1, 2, 3"),
    (LEAD, "1"),
    (LEAD, ""),
])
def test_a_miscount_is_one_arity_diagnostic(analyze, source, args):
    assert _codes(analyze(source.format(args=args))) == ["CE2009"]


@pytest.mark.parametrize("source, args", [
    (PAIR, "1, 2"),
    (LEAD, "1, 2"),
    (LEAD, "1, 2, true, 3"),
])
def test_control_a_right_count_is_silent(analyze, source, args):
    assert _codes(analyze(source.format(args=args))) == []


def test_control_a_count_that_fits_and_does_not_infer_is_still_ce2060(analyze):
    source = PAIR.format(args='1, "two"')
    assert _codes(analyze(source)) == ["CE2060"]
