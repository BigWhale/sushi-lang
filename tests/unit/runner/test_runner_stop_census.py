"""A run prints the stop-code census of its red fixtures.

The census sorts each failed fixture whose compiler output holds the stop code CE0155 by
the stage and the construct that the code names, and prints the table at the end of the
run. It reads the results and decides nothing: the exit code is the one of the failures.

The stub `fixtures/stop_stub.py` stands in for the compiler. Twelve fixtures expect the
error CE1001: two stop at `typecheck, Match`, one at `emit, Trap`, two at constructs of two
words, two at an error hole, three at a construct inside a bundled stdlib unit (two in one
unit and one in another), one fails with another
code, and one prints CE1001 and passes. A stop inside a bundled stdlib unit has a row of its
own, `<stage>, <construct> [<module>]`.
"""
from __future__ import annotations

import os
import shutil

import pytest

from _harness import FIXTURES_DIR, TESTS_DIR, run_tests
from fork_server import COMPILER_STUB_ENV

STUB = FIXTURES_DIR / "stop_stub.py"
FIXTURES = {
    "test_err_census_match_a": "typecheck, Match",
    "test_err_census_match_b": "typecheck, Match",
    "test_err_census_trap": "emit, Trap",
    "test_err_census_view": "slir, borrowed view",
    "test_err_census_rebind": "slir, borrowed rebind",
    "test_err_census_hole_a": "lowering, error hole, Match with no CE2040 or CE2118",
    "test_err_census_hole_b": "lowering, error hole, Call with no CE2001",
    "test_err_census_unit_a": "typecheck, Call(method) @ io/error",
    "test_err_census_unit_b": "typecheck, Call(method) @ io/error",
    "test_err_census_unit_c": "typecheck, Match @ net/error",
    "test_err_census_other": "CE1002",
    "test_err_census_pass": "CE1001",
}


@pytest.fixture
def staged():
    marker = f"stop_census_tmp_{os.getpid()}"
    directory = TESTS_DIR / marker
    directory.mkdir()
    for name, what in FIXTURES.items():
        (directory / f"{name}_{os.getpid()}.sushi").write_text(
            f"# EXPECT_ERROR_CODE: CE1001\n\nfn main() i32:\n    return 0\n# STUB: {what}\n",
            encoding="utf-8")
    try:
        yield marker
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _run(staged, *flags):
    env = {**os.environ, COMPILER_STUB_ENV: str(STUB)}
    return run_tests("--skip-build", "--filter", staged, *flags, env=env)


def test_the_census_counts_each_stop_and_each_other_failure(staged):
    done = _run(staged)
    assert done.returncode == 1, done.stdout[-3000:] + done.stderr[-3000:]
    assert "Passed: 1" in done.stdout and "Failed: 11" in done.stdout, done.stdout[-3000:]
    table = done.stdout.split("Stop-code census", 1)[1].splitlines()
    assert table[0].strip() == "(10 stopped, 1 failed with no stop code):", table[:12]
    rows = sorted(line.split(None, 2) for line in table[1:8])
    assert rows == sorted([["2", "typecheck", "Match"], ["2", "lowering", "error hole"],
                           ["2", "typecheck", "Call(method) [io/error]"],
                           ["1", "emit", "Trap"], ["1", "slir", "borrowed view"],
                           ["1", "slir", "borrowed rebind"],
                           ["1", "typecheck", "Match [net/error]"]]), table[:12]
    assert [line.split()[0] for line in table[1:8]] == ["2", "2", "2", "1", "1", "1", "1"]


def test_a_run_with_no_stop_prints_no_census(staged):
    for name, what in FIXTURES.items():
        if what.startswith("CE"):
            continue
        for path in (TESTS_DIR / staged).glob(f"{name}_*.sushi"):
            path.unlink()
    done = _run(staged)
    assert "Stop-code census" not in done.stdout, done.stdout[-3000:]


def test_the_json_output_stays_clean(staged):
    done = _run(staged, "--json")
    assert "Stop-code census" not in done.stdout
    assert done.stdout.lstrip().startswith("{"), done.stdout[:200]
