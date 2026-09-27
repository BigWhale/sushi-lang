"""`OUTPUT_PATH`, the directive that names the `-o` path, red and green, through the real runner (#1006).

The runner owns `-o`, so a fixture could not state an output path whose directory does not
exist. `OUTPUT_PATH: <relative path>` makes the runner pass `-o <the fixture's copy>/<path>`.
The runner never creates the path's parent: that directory is the subject of the test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[2]

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
from test_metadata import parse_test_metadata  # noqa: E402

EXITS_0 = "fn main() i32:\n    return Result.Ok(0)\n"
PRINTS = 'fn main() i32:\n    println("Mostly Harmless")\n    return Result.Ok(0)\n'
LIBRARY = "public fn answer() i32:\n    return Result.Ok(42)\n"
TWO_UNITS = 'use "dep"\n\nfn main() i32:\n    println("{N}")\n    return Result.Ok(0)\n'
DEP = "public const i32 N = 1\n"
REFUSED = ("# EXPECT_ERROR_CODES_EXACT: CE3019\n"
           '# EXPECT_STDERR_CONTAINS: "is not a directory"\n')
IN_DIR = "# RUN_IN_FIXTURE_DIR\n"
LIB = "# COMPILER_FLAGS: --lib --lib-version 1.0.0\n"


def _fixture(tmp_path: Path, name: str, header: str, body: str = EXITS_0,
             extra: dict | None = None, dirs: tuple = ()) -> Path:
    home = tmp_path / "fixture"
    home.mkdir()
    (home / "dep.sushi").write_text(DEP, encoding="utf-8")
    for rel, text in (extra or {}).items():
        (home / rel).write_text(text, encoding="utf-8")
    for rel in dirs:
        (home / rel).mkdir(parents=True)
    path = home / name
    path.write_text(header + "\n" + body, encoding="utf-8")
    return path


def _run(path: Path):
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        return runner.run_single_test(path)


def _detail(result) -> str:
    return f"{result.compilation_message}\n{result.runtime_message}"


def _spy_output(monkeypatch) -> list[tuple[str, bool, str]]:
    """Each compilation's `-o` spelling, whether its parent existed then, and the cwd."""
    seen: list[tuple[str, bool, str]] = []
    real_run = enhanced_test_runner.subprocess.run

    def _spy(cmd, *args, **kwargs):
        cmd = [str(c) for c in cmd]
        if cmd[0].endswith("sushic") and "-o" in cmd:
            out = cmd[cmd.index("-o") + 1]
            cwd = str(kwargs.get("cwd"))
            seen.append((out, (Path(cwd) / out).parent.exists(), cwd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(enhanced_test_runner.subprocess, "run", _spy)
    return seen


def test_the_directive_parses(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "test_err_o.sushi",
                                        "# OUTPUT_PATH: nodir/out\n"))
    assert meta.output_path == "nodir/out"
    assert meta.directive_errors == []


def test_no_directive_is_nothing(tmp_path):
    assert parse_test_metadata(_fixture(tmp_path, "test_o.sushi", "")).output_path is None


def test_output_path_lets_lib_through_compiler_flags(tmp_path, capsys):
    meta = parse_test_metadata(_fixture(tmp_path, "test_err_o.sushi",
                                        "# OUTPUT_PATH: nodir/x.slib\n" + LIB))
    assert meta.compiler_flags == ["--lib", "--lib-version", "1.0.0"]
    assert "--lib" not in capsys.readouterr().err


def test_output_path_below_compiler_flags_lets_lib_through_too(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "test_err_o.sushi",
                                        LIB + "# OUTPUT_PATH: nodir/x.slib\n"))
    assert meta.compiler_flags == ["--lib", "--lib-version", "1.0.0"]


def test_output_path_never_lets_o_through(tmp_path, capsys):
    meta = parse_test_metadata(_fixture(tmp_path, "test_err_o.sushi",
                                        "# OUTPUT_PATH: nodir/x\n# COMPILER_FLAGS: -o y\n"))
    assert "-o" not in meta.compiler_flags
    assert "-o" in capsys.readouterr().err


# -- the refusal, one per output kind ------------------------------------------

@pytest.mark.parametrize("header, body, extra", [
    ("# OUTPUT_PATH: nodir/out\n", EXITS_0, {}),
    ("# OUTPUT_PATH: nodir/x.slib\n" + LIB, LIBRARY, {}),
    ("# OUTPUT_PATH: nodir/out\n", TWO_UNITS, {}),
    ("# OUTPUT_PATH: afile/out\n", EXITS_0, {"afile": "a regular file\n"}),
    (IN_DIR + "# OUTPUT_PATH: nodir/out\n", TWO_UNITS, {}),
], ids=["a program", "a library", "two units", "a parent that is a file",
        "in the fixture's directory"])
def test_a_missing_directory_is_ce3019(tmp_path, monkeypatch, header, body, extra):
    seen = _spy_output(monkeypatch)
    result = _run(_fixture(tmp_path, "test_err_o.sushi", header + REFUSED, body, extra))
    assert result.total_success, _detail(result)
    (out, parent_existed, _), = seen
    assert out.endswith("/out") or out.endswith("/x.slib"), out
    if "afile" in header:
        assert parent_existed, "the fixture's own file is the parent"
    else:
        assert not parent_existed, "the runner created the output path's parent"


def test_the_expectation_fails_where_the_directory_exists(tmp_path):
    """The directive reaches the compiler: an existing `nodir/` builds, and CE3019 is absent."""
    path = _fixture(tmp_path, "test_err_o.sushi", "# OUTPUT_PATH: nodir/out\n" + REFUSED,
                    dirs=("nodir",))
    result = _run(path)
    assert not result.total_success
    assert "Expected 2, got 0" in result.compilation_message, result.compilation_message


def test_the_spelling_is_absolute_outside_the_fixtures_directory(tmp_path, monkeypatch):
    seen = _spy_output(monkeypatch)
    _run(_fixture(tmp_path, "test_err_o.sushi", "# OUTPUT_PATH: nodir/out\n" + REFUSED))
    (out, _, _), = seen
    assert Path(out).is_absolute() and out.endswith("/fixture/nodir/out"), out


def test_the_spelling_is_relative_in_the_fixtures_directory(tmp_path, monkeypatch):
    seen = _spy_output(monkeypatch)
    _run(_fixture(tmp_path, "test_err_o.sushi",
                  IN_DIR + "# OUTPUT_PATH: nodir/out\n" + REFUSED))
    (out, _, cwd), = seen
    assert out == "nodir/out", out
    assert cwd.endswith("/fixture"), cwd


# -- a fixture that expects success ---------------------------------------------

def test_a_success_fixture_runs_the_binary_at_the_output_path(tmp_path, monkeypatch):
    seen = _spy_output(monkeypatch)
    path = _fixture(tmp_path, "test_o.sushi",
                    '# OUTPUT_PATH: out/x\n# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n',
                    PRINTS, dirs=("out",))
    result = _run(path)
    assert result.total_success, _detail(result)
    assert result.runtime_success is True, _detail(result)
    (out, parent_existed, _), = seen
    assert out.endswith("/fixture/out/x") and parent_existed, out


def test_a_success_fixture_reads_its_output_through_expect_path_exists(tmp_path):
    path = _fixture(tmp_path, "test_o.sushi",
                    "# OUTPUT_PATH: out/x\n# EXPECT_PATH_EXISTS: out/x\n", dirs=("out",))
    result = _run(path)
    assert result.total_success, _detail(result)


def test_a_success_fixture_whose_directory_is_missing_fails(tmp_path):
    result = _run(_fixture(tmp_path, "test_o.sushi", "# OUTPUT_PATH: nodir/out\n"))
    assert not result.total_success
    assert "CE3019" in result.compilation_message, result.compilation_message


def test_a_library_with_a_run_is_refused(tmp_path):
    """A library has no binary: a fixture that would run one says so before it compiles."""
    path = _fixture(tmp_path, "test_o.sushi", "# OUTPUT_PATH: out/x.slib\n" + LIB, LIBRARY,
                    dirs=("out",))
    result = _run(path)
    assert not result.total_success
    assert "a library has no binary to run" in result.compilation_message, \
        result.compilation_message


# -- a path outside the copy ----------------------------------------------------

@pytest.mark.parametrize("value", ["/tmp/out", "../out", "nodir/../../out", ""],
                         ids=["absolute", "above the copy", "climbs out", "empty"])
def test_a_path_outside_the_copy_fails_the_fixture(tmp_path, value):
    result = _run(_fixture(tmp_path, "test_err_o.sushi",
                           f"# OUTPUT_PATH: {value}\n" + REFUSED))
    assert not result.total_success
    assert "OUTPUT_PATH" in result.compilation_message, result.compilation_message
