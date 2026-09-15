"""The `entrypoint` pass is the one home of main's rule (#674).

The pass is named for main's signature and used to check none of it: it set one bool,
`main_expects_args`, and the three rules that answer a diagnostic lived elsewhere -- "a
main exists" and "a library carries none" in `compiler/pipeline.py`, "main returns an
integer" in the collect pass. The pass named for the rule held the least of it.

All four are the pass's now, in the ruled order: CE3007, CE3501, CE0106, then the
`string[] args` read.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit.sushic_path import SUSHIC

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIB_WITH_MAIN = PROJECT_ROOT / "tests" / "units" / "entrypoint" / "helpers" / "lib_with_main.sushi"

NO_MAIN = """
fn helper(i32 a) i32:
    return Result.Ok(a + 1)
"""

MAIN_RETURNS_UNIT = """
fn main() ~:
    return Result.Ok(~)
"""

MAIN_WITH_ARGS = """
fn main(string[] args) i32:
    return Result.Ok(0)
"""

PLAIN_MAIN = """
fn main() i32:
    return Result.Ok(0)
"""


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def test_a_missing_main_is_the_pass_answer(analyze):
    """CE3007: an executable needs an entry point, and the pass says so."""
    reporter = analyze(NO_MAIN)

    assert "CE3007" in _codes(reporter)


def test_a_library_may_not_carry_main(analyze):
    """CE3501: the library build refuses it, and the pass knows which build this is."""
    reporter = analyze(PLAIN_MAIN, is_library=True)

    assert "CE3501" in _codes(reporter)


def test_a_library_without_main_hears_nothing(analyze):
    """No CE3007 for a library: the rule is the executable's."""
    reporter = analyze(NO_MAIN, is_library=True)

    assert "CE3007" not in _codes(reporter)


def test_main_must_return_an_integer(analyze):
    """CE0106, from the pass and not from the collect walk over every function."""
    reporter = analyze(MAIN_RETURNS_UNIT)

    assert "CE0106" in _codes(reporter)


def test_the_library_rule_is_answered_before_the_return_type(analyze):
    """The ruled order: a main in a library is refused, then its return is read."""
    reporter = analyze(MAIN_RETURNS_UNIT, is_library=True)

    codes = [code for code in _codes(reporter) if code in {"CE3501", "CE0106"}]
    assert codes == ["CE3501", "CE0106"]


@pytest.mark.parametrize("src, expects", [(MAIN_WITH_ARGS, True), (PLAIN_MAIN, False)],
                         ids=["with-args", "no-args"])
def test_the_pass_reads_the_args_parameter(analyze_program, src, expects):
    """The fourth check, unchanged: the back end reads this bool."""
    analysis = analyze_program(src)

    assert analysis.analyzer.main_expects_args is expects


def test_a_library_build_refuses_main_end_to_end(tmp_path):
    """The whole compiler over `--lib`: CE3501 and exit 2."""
    result = subprocess.run(
        [str(SUSHIC), "--lib", "--lib-version", "0.0.0", str(LIB_WITH_MAIN),
         "-o", str(tmp_path / "libmain.slib")],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)

    assert result.returncode == 2
    assert "CE3501" in result.stderr
