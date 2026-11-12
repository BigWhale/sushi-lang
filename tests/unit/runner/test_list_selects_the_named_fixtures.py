"""`--list FILE` runs exactly the fixtures that the file names (the ratchet list).

The file holds one repository-relative path for each line. A blank line and a `#` line are
not paths. `--list` is a selector like every other flag: it goes through `select_fixtures`
and narrows WHICH fixtures run, never how hard they are checked. A listed path that names
no fixture is an error that fails the run, and a list that selects nothing fails the run
(#765). A list names fixtures and nothing else, so the runner steps that check the stdlib,
the library reader and the toolchain programs print their skip.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _harness import TESTS_DIR, run_tests
from test_metadata import FixtureListError, read_fixture_list, select_fixtures

FIRST = "tests/diagnostics/borrow_help/test_err_borrow_help_spells_a_member_chain.sushi"
SECOND = "tests/generics/builtin_methods/test_err_result_is_ok_arity.sushi"
MISSING = "tests/generics/builtin_methods/test_err_no_such_fixture.sushi"
GATES = ("Stdlib doc-block gate", "Library-reader gate", "Library-report gate",
         "Dead-code gate")


def _list(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "ratchet.txt"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def test_the_list_selects_exactly_the_named_fixtures():
    chosen = select_fixtures(TESTS_DIR, listed=[SECOND, FIRST])
    assert [p.relative_to(TESTS_DIR.parent).as_posix() for p in chosen] == [FIRST, SECOND]


def test_a_filter_narrows_the_list_further():
    chosen = select_fixtures(TESTS_DIR, listed=[FIRST, SECOND], filter_pattern="generics/")
    assert [p.relative_to(TESTS_DIR.parent).as_posix() for p in chosen] == [SECOND]


def test_a_path_that_names_no_fixture_is_an_error():
    with pytest.raises(FixtureListError) as caught:
        select_fixtures(TESTS_DIR, listed=[FIRST, MISSING])
    assert MISSING in str(caught.value)


def test_a_helper_unit_is_not_a_fixture():
    """The collector steps over `helpers/`, so a list cannot name a file there."""
    helper = next(p for p in sorted((TESTS_DIR / "libs" / "helpers").glob("*.sushi")))
    with pytest.raises(FixtureListError):
        select_fixtures(TESTS_DIR, listed=[helper.relative_to(TESTS_DIR.parent).as_posix()])


def test_the_file_skips_blank_lines_and_comments(tmp_path):
    path = _list(tmp_path, "# the ratchet", "", f"  {FIRST}  ", "   # indented", SECOND)
    assert read_fixture_list(path) == [FIRST, SECOND]


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(FixtureListError) as caught:
        read_fixture_list(tmp_path / "absent.txt")
    assert "absent.txt" in str(caught.value)


def test_the_front_end_runs_the_listed_fixtures_and_skips_the_steps(tmp_path):
    done = run_tests("--skip-build", "--list", str(_list(tmp_path, FIRST, SECOND)))
    assert done.returncode == 0, done.stdout[-3000:]
    assert "Total tests: 2" in done.stdout and "Passed: 2" in done.stdout, done.stdout[-3000:]
    for gate in GATES:
        assert f"{gate} SKIPPED: --list" in done.stdout, (gate, done.stdout[-3000:])


def test_a_listed_path_that_names_no_fixture_fails_the_run(tmp_path):
    done = run_tests("--skip-build", "--list", str(_list(tmp_path, FIRST, MISSING)))
    assert done.returncode == 1, done.stdout[-3000:]
    assert MISSING in done.stdout + done.stderr, done.stdout[-3000:] + done.stderr[-3000:]
    assert "Total tests" not in done.stdout, "no fixture may run from a bad list"


def test_a_list_that_selects_nothing_fails_the_run(tmp_path):
    done = run_tests("--skip-build", "--list", str(_list(tmp_path, "# nothing yet")))
    assert done.returncode == 1, done.stdout[-3000:]
    assert "--list" in done.stdout, done.stdout[-3000:]


def test_a_bad_list_is_a_json_report(tmp_path):
    done = run_tests("--skip-build", "--json", "--list", str(_list(tmp_path, MISSING)))
    assert done.returncode == 1, done.stdout[-3000:]
    report = json.loads(done.stdout)
    assert MISSING in report["list_error"], report
