"""Each directive the runner honours, red and green, through the real runner (#959).

A directive the runner parses and then ignores asserts nothing, and a fixture that carries
it passes whatever the compiler does. So each one gets a small program written in a
temporary directory and run by `TestRunner.run_single_test`, once where the directive
holds and once where it does not. The directives that already had both cases elsewhere in
this set are not repeated: the red half of `EXPECT_NO_LEAKS` (`test_leak_gate_wiring.py`)
and of `EXPECT_NO_OPEN_FDS` (`test_fd_gate_wiring.py`), and `--filter` as a path
substring (`test_fixture_selection_is_one.py`).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
from run_tests import SPELLING_GATE_ENV, build_leakcheck  # noqa: E402
from test_metadata import parse_test_metadata  # noqa: E402

PRINTS = 'fn main() i32:\n    println("Mostly Harmless")\n    return Result.Ok(0)\n'
EXITS_3 = "fn main() i32:\n    return Result.Ok(3)\n"
EXITS_0 = "fn main() i32:\n    return Result.Ok(0)\n"
WRITES_STDERR = (
    "use <io/fs>\n\n"
    "fn main() i32:\n"
    '    match stderr.write("Mostly Harmless\\n"):\n'
    "        Result.Ok(_) -> return Result.Ok(0)\n"
    "        Result.Err(_) -> return Result.Ok(4)\n"
)
WARNS = "fn main() i32:\n    let i32 x = 1\n    return Result.Ok(0)\n"
UNDECLARED = "fn main() i32:\n    println(y)\n    return Result.Ok(0)\n"
# Three diagnostics: CW1001, CE1001 and CE2002.
THREE_CODES = "fn main() i32:\n    let i32 x = y<3\n    return Result.Ok(0)\n"
UNDOCUMENTED = "fn helper() i32:\n    return Result.Ok(1)\n\n" + EXITS_0
# The unit name is user text, and the spelling gate reads `a<b` as an interned type name.
TRIPS_THE_SPELLING_GATE = 'use "a<b"\n\n' + EXITS_0


def _run(tmp_path: Path, name: str, header: str, body: str):
    path = tmp_path / name
    path.write_text(header + "\n" + body, encoding="utf-8")
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        return runner.run_single_test(path)


CASES = [
    # (id, file name, header, body, passes)
    ("runtime exit, held", "test_exit.sushi", "# EXPECT_RUNTIME_EXIT: 3\n", EXITS_3, True),
    ("runtime exit, broken", "test_exit.sushi", "# EXPECT_RUNTIME_EXIT: 4\n", EXITS_3, False),
    ("stdout exact, held", "test_out.sushi",
     '# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n', PRINTS, True),
    ("stdout exact, broken", "test_out.sushi",
     '# EXPECT_STDOUT_EXACT: "Mostly Harmless"\n', PRINTS, False),
    ("stdout contains, held", "test_out.sushi",
     '# EXPECT_STDOUT_CONTAINS: "Harmless"\n', PRINTS, True),
    ("stdout contains, broken", "test_out.sushi",
     '# EXPECT_STDOUT_CONTAINS: "Harmful"\n', PRINTS, False),
    ("runtime stderr contains, held", "test_e_out.sushi",
     '# EXPECT_STDERR_CONTAINS: "Mostly Harmless"\n', WRITES_STDERR, True),
    ("runtime stderr contains, broken", "test_e_out.sushi",
     '# EXPECT_STDERR_CONTAINS: "Harmful"\n', WRITES_STDERR, False),
    ("compile stderr contains, held", "test_err_undeclared.sushi",
     "# EXPECT_STDERR_CONTAINS: \"undeclared identifier 'y'\"\n", UNDECLARED, True),
    ("compile stderr contains, broken", "test_err_undeclared.sushi",
     "# EXPECT_STDERR_CONTAINS: \"undeclared identifier 'z'\"\n", UNDECLARED, False),
    ("stderr empty, held", "test_quiet.sushi",
     '# EXPECT_STDERR_EMPTY: true\n# EXPECT_STDOUT_CONTAINS: "Mostly"\n', PRINTS, True),
    ("stderr empty, broken", "test_noisy.sushi",
     "# EXPECT_STDERR_EMPTY: true\n", WRITES_STDERR, False),
    ("error code, held", "test_err_undeclared.sushi",
     "# EXPECT_ERROR_CODE: CE1001\n", UNDECLARED, True),
    ("error code, broken", "test_err_undeclared.sushi",
     "# EXPECT_ERROR_CODE: CE2002\n", UNDECLARED, False),
    ("compiler flags, held", "test_warn_undocumented.sushi",
     "# COMPILER_FLAGS: --warn-missing-docs\n# EXPECT_ERROR_CODE: CW7002\n",
     UNDOCUMENTED, True),
    ("compiler flags, absent", "test_warn_undocumented.sushi",
     "# EXPECT_ERROR_CODE: CW7002\n", UNDOCUMENTED, False),
    ("test_ must exit 0, held", "test_clean.sushi",
     '# EXPECT_STDOUT_CONTAINS: "Mostly"\n', PRINTS, True),
    ("test_ must exit 0, a warning", "test_warns.sushi", "", WARNS, False),
    ("test_warn_ must exit 1, held", "test_warn_warns.sushi",
     "# EXPECT_ERROR_CODE: CW1001\n", WARNS, True),
    ("test_warn_ must exit 1, clean", "test_warn_clean.sushi", "", PRINTS, False),
    ("test_err_ must exit 2, held", "test_err_fails.sushi",
     "# EXPECT_ERROR_CODE: CE1001\n", UNDECLARED, True),
    ("test_err_ must exit 2, a warning", "test_err_warns.sushi", "", WARNS, False),
    ("test_err_ must exit 2, clean", "test_err_clean.sushi", "", PRINTS, False),
    ("exact code set, held", "test_err_exact.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CE1001\n", UNDECLARED, True),
    ("exact code set, every code named", "test_err_exact.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CW1001, CE1001, CE2002\n", THREE_CODES, True),
    ("exact code set, the directive repeated", "test_err_exact.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CE1001\n# EXPECT_ERROR_CODES_EXACT: CE2002 CW1001\n",
     THREE_CODES, True),
    ("exact code set, codes beside it", "test_err_exact.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CE1001\n", THREE_CODES, False),
    ("exact code set, a code missing", "test_err_exact.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CE1001, CE2009\n", UNDECLARED, False),
    ("exact code set, on a warning test", "test_warn_exact.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CW1001\n", WARNS, True),
    ("exact code set, a warning left out", "test_err_exact.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CE1001, CE2002\n", THREE_CODES, False),
]


@pytest.mark.parametrize("name, header, body, passes",
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_the_directive_decides_the_verdict(tmp_path, name, header, body, passes):
    result = _run(tmp_path, name, header, body)
    detail = f"{result.compilation_message}\n{result.runtime_message}"
    assert result.total_success is passes, detail


def test_a_substring_code_check_cannot_pin_the_set(tmp_path):
    """Why the exact directive exists: CE1001 passes beside two codes nobody asked for."""
    assert _run(tmp_path, "test_err_substring.sushi", "# EXPECT_ERROR_CODE: CE1001\n",
                THREE_CODES).total_success


def test_the_exact_directive_names_what_differs(tmp_path):
    result = _run(tmp_path, "test_err_exact.sushi", "# EXPECT_ERROR_CODES_EXACT: CE1001, CE2009\n",
                  THREE_CODES)
    assert not result.total_success
    assert "CE2009" in result.compilation_message, result.compilation_message
    assert "CE2002" in result.compilation_message, result.compilation_message


def test_the_exact_directive_parses(tmp_path):
    path = tmp_path / "test_err_parse.sushi"
    path.write_text("# EXPECT_ERROR_CODES_EXACT: CE1001, CE2002\n"
                    "# EXPECT_ERROR_CODES_EXACT: CW1001\n\n" + UNDECLARED, encoding="utf-8")
    assert parse_test_metadata(path).expect_error_codes_exact == ["CE1001", "CE2002", "CW1001"]


def test_no_exact_directive_is_no_exact_check(tmp_path):
    path = tmp_path / "test_err_parse.sushi"
    path.write_text("# EXPECT_ERROR_CODE: CE1001\n\n" + UNDECLARED, encoding="utf-8")
    assert parse_test_metadata(path).expect_error_codes_exact is None


def test_the_spelling_gate_fails_a_fixture_whose_exit_code_matches(tmp_path, monkeypatch):
    """The gate kills the compiler with exit 1: a `test_warn_` exit, and still a failure."""
    monkeypatch.setenv(SPELLING_GATE_ENV, "1")
    result = _run(tmp_path, "test_warn_spelling.sushi", "", TRIPS_THE_SPELLING_GATE)
    assert not result.total_success
    assert "spelling gate" in result.compilation_message, result.compilation_message


def test_the_armed_spelling_gate_passes_a_clean_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv(SPELLING_GATE_ENV, "1")
    result = _run(tmp_path, "test_spelling_clean.sushi",
                  '# EXPECT_STDOUT_CONTAINS: "Mostly"\n', PRINTS)
    assert result.total_success, result.compilation_message


@pytest.fixture(scope="module")
def interposer():
    if enhanced_test_runner.leakcheck_platform() is None:
        pytest.skip("leak checking is not supported on this platform")
    assert build_leakcheck(PROJECT_ROOT), "the interposer must build"


def test_a_clean_program_passes_the_leak_check(tmp_path, interposer):
    result = _run(tmp_path, "test_no_leak.sushi",
                  '# EXPECT_NO_LEAKS\n# EXPECT_STDOUT_CONTAINS: "Mostly"\n', PRINTS)
    assert result.total_success, result.runtime_message
    assert "no leaks" in result.runtime_message, result.runtime_message


def test_a_program_that_closes_its_descriptors_passes_the_fd_check(tmp_path, interposer):
    result = _run(tmp_path, "test_no_open_fd.sushi",
                  '# EXPECT_NO_OPEN_FDS\n# EXPECT_STDOUT_CONTAINS: "Mostly"\n', PRINTS)
    assert result.total_success, result.runtime_message
    assert "no leaks" in result.runtime_message, result.runtime_message


def test_compile_only_runs_no_binary(tmp_path, monkeypatch):
    """`--compile-only` selects the fixtures that never run, and runs nothing."""
    for name, header, body in [
        ("test_err_only.sushi", "# EXPECT_ERROR_CODE: CE1001\n", UNDECLARED),
        ("test_warn_only.sushi", "# EXPECT_ERROR_CODE: CW1001\n", WARNS),
        ("test_runs.sushi", '# EXPECT_STDOUT_CONTAINS: "Mostly"\n', PRINTS),
    ]:
        (tmp_path / name).write_text(header + "\n" + body, encoding="utf-8")

    ran: list[str] = []

    def _record(self, test_name, test_file, metadata):
        ran.append(test_name)
        return True, "recorded"

    monkeypatch.setattr(enhanced_test_runner.TestRunner, "_run_runtime_test", _record)
    with enhanced_test_runner.TestRunner(tmp_path, project_root=PROJECT_ROOT,
                                         compile_only=True) as runner:
        results = runner.run_all_tests()
    assert sorted(results) == ["test_err_only.sushi", "test_warn_only.sushi"]
    assert all(r.total_success for r in results.values()), results
    assert ran == []

    with enhanced_test_runner.TestRunner(tmp_path, project_root=PROJECT_ROOT) as runner:
        runner.run_all_tests()
    assert ran == ["test_runs.sushi"], "the control: a full run does run the binary"
