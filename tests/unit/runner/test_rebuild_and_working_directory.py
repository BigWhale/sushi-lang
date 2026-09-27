"""The rebuild fixture and `RUN_IN_FIXTURE_DIR`, red and green, through the real runner (#988).

A rebuild fixture is a directory: the fixture, the units it imports, and a `v2/` directory
whose files replace their namesakes before a second compilation. The runner compiles it
twice in a copy of the directory, with a cache of its own, and reads which units the
second compilation served `[cached]` and which it `[rebuilt]`. `RUN_IN_FIXTURE_DIR` starts
`sushic` from the fixture's directory with a relative source path, the way a user does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = TESTS_DIR.parent

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
from test_metadata import collect_fixtures, parse_test_metadata  # noqa: E402

MAIN = 'use "dep"\n\nfn main() i32:\n    println("{N}")\n    return Result.Ok(0)\n'
DEP_V1 = "public const i32 N = 1\n"
DEP_V2 = "public const i32 N = 2\n"


def _fixture(tmp_path: Path, header: str, v2: dict | None = None,
             name: str = "test_rebuild.sushi", extra: dict | None = None) -> Path:
    home = tmp_path / "fixture"
    home.mkdir()
    (home / name).write_text(header + "\n" + MAIN, encoding="utf-8")
    (home / "dep.sushi").write_text(DEP_V1, encoding="utf-8")
    for rel, body in (extra or {}).items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(body, encoding="utf-8")
    if v2 is not None:
        (home / "v2").mkdir()
        for rel, body in v2.items():
            (home / "v2" / rel).write_text(body, encoding="utf-8")
    return home / name


def _run(path: Path):
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        return runner.run_single_test(path)


def _detail(result) -> str:
    return f"{result.compilation_message}\n{result.runtime_message}"


HELD = ('# EXPECT_STDOUT_EXACT_BEFORE_REBUILD: "1\\n"\n'
        '# EXPECT_STDOUT_EXACT: "2\\n"\n'
        "# EXPECT_REBUILT: dep, test_rebuild\n")


def test_a_rebuild_that_holds_passes(tmp_path):
    result = _run(_fixture(tmp_path, HELD, {"dep.sushi": DEP_V2}))
    assert result.total_success, _detail(result)


@pytest.mark.parametrize("header, says", [
    (HELD.replace("EXPECT_REBUILT: dep, test_rebuild",
                  "EXPECT_REBUILT: dep\n# EXPECT_CACHED: test_rebuild"),
     "test_rebuild"),
    (HELD.replace("EXPECT_REBUILT: dep, test_rebuild", "EXPECT_REBUILT: dep"),
     "test_rebuild"),
    (HELD.replace("EXPECT_REBUILT: dep, test_rebuild",
                  "EXPECT_REBUILT: dep, test_rebuild, ghost"),
     "ghost"),
    (HELD.replace('EXPECT_STDOUT_EXACT: "2\\n"', 'EXPECT_STDOUT_EXACT: "1\\n"'), "Stdout"),
    (HELD.replace('BEFORE_REBUILD: "1\\n"', 'BEFORE_REBUILD: "2\\n"'), "before the rebuild"),
], ids=["a rebuilt unit named cached", "a rebuilt unit not named", "a unit never reported",
        "the stdout after the rebuild", "the stdout before the rebuild"])
def test_a_rebuild_that_does_not_hold_fails(tmp_path, header, says):
    result = _run(_fixture(tmp_path, header, {"dep.sushi": DEP_V2}))
    assert not result.total_success, _detail(result)
    assert says in _detail(result), _detail(result)


def test_an_unchanged_rebuild_is_all_cached(tmp_path):
    header = '# EXPECT_STDOUT_EXACT: "1\\n"\n# EXPECT_CACHED: dep, test_rebuild\n'
    result = _run(_fixture(tmp_path, header, {"dep.sushi": DEP_V1}))
    assert result.total_success, _detail(result)


def test_a_rebuild_directive_without_v2_fails(tmp_path):
    result = _run(_fixture(tmp_path, HELD))
    assert not result.total_success
    assert "v2/" in result.compilation_message, result.compilation_message


def test_v2_may_not_replace_the_fixture_itself(tmp_path):
    result = _run(_fixture(tmp_path, HELD, {"dep.sushi": DEP_V2,
                                            "test_rebuild.sushi": MAIN}))
    assert not result.total_success
    assert "v2/" in result.compilation_message, result.compilation_message


def test_the_fixture_directory_is_never_written(tmp_path):
    path = _fixture(tmp_path, HELD, {"dep.sushi": DEP_V2})
    before = sorted(p.relative_to(path.parent) for p in path.parent.rglob("*"))
    assert _run(path).total_success
    assert sorted(p.relative_to(path.parent) for p in path.parent.rglob("*")) == before
    assert (path.parent / "dep.sushi").read_text(encoding="utf-8") == DEP_V1


def test_v2_is_never_a_fixture_of_its_own(tmp_path):
    tests = tmp_path / "tests"
    (tests / "cache" / "x" / "v2").mkdir(parents=True)
    (tests / "cache" / "x" / "test_x.sushi").write_text(MAIN, encoding="utf-8")
    (tests / "cache" / "x" / "v2" / "test_y.sushi").write_text(MAIN, encoding="utf-8")
    assert [p.name for p in collect_fixtures(tests)] == ["test_x.sushi"]


def test_the_directives_parse(tmp_path):
    path = tmp_path / "test_parse.sushi"
    path.write_text(
        "# EXPECT_REBUILT: a, b\n# EXPECT_REBUILT: c\n# EXPECT_CACHED: d e\n"
        '# EXPECT_STDOUT_EXACT_BEFORE_REBUILD: "x\\n"\n# RUN_IN_FIXTURE_DIR\n'
        "# BUILD_LIB: shapes.sushi\n# STDLIB_MODULE: fixture/consts=consts.sushi\n\n"
        + MAIN, encoding="utf-8")
    meta = parse_test_metadata(path)
    assert meta.expect_rebuilt == ["a", "b", "c"]
    assert meta.expect_cached == ["d", "e"]
    assert meta.expect_stdout_exact_before_rebuild == "x\n"
    assert meta.run_in_fixture_dir is True
    assert meta.build_libs == ["shapes.sushi"]
    assert meta.stdlib_modules == {"fixture/consts": "consts.sushi"}


def test_no_directive_is_no_rebuild(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, '# EXPECT_STDOUT_EXACT: "1\\n"\n'))
    assert meta.expect_rebuilt is None and meta.expect_cached is None
    assert meta.run_in_fixture_dir is False


# -- the working directory ----------------------------------------------------

READS_DATA = (
    "use <io/fs>\n\n"
    "fn main() i32:\n"
    '    match open("data.txt", FileMode.Read()):\n'
    "        Result.Ok(nom f) ->\n"
    "            match f.read_all():\n"
    '                Result.Ok(text) -> print(text)\n'
    '                Result.Err(_) -> println("unreadable")\n'
    '        Result.Err(_) -> println("no data.txt here")\n'
    "    return Result.Ok(0)\n"
)


def _cwd_fixture(tmp_path: Path, header: str) -> Path:
    home = tmp_path / "cwd_fixture"
    home.mkdir()
    (home / "data.txt").write_text("Mostly Harmless\n", encoding="utf-8")
    path = home / "test_in_its_dir.sushi"
    path.write_text(header + "\n" + READS_DATA, encoding="utf-8")
    return path


def test_run_in_fixture_dir_runs_the_binary_there(tmp_path):
    header = '# RUN_IN_FIXTURE_DIR\n# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n'
    result = _run(_cwd_fixture(tmp_path, header))
    assert result.total_success, _detail(result)


def test_without_the_directive_the_binary_runs_elsewhere(tmp_path):
    """The control: the same fixture, run from the runner's directory, finds no file."""
    result = _run(_cwd_fixture(tmp_path, '# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n'))
    assert not result.total_success
    assert "no data.txt here" in _detail(result), _detail(result)


def test_run_in_fixture_dir_starts_sushic_there_with_a_relative_path(tmp_path, monkeypatch):
    seen: list[tuple[list[str], str, bool]] = []
    real_run = enhanced_test_runner.subprocess.run

    def _spy(cmd, *args, **kwargs):
        if any(str(c).endswith("sushic") for c in cmd):
            cwd = Path(kwargs["cwd"])
            seen.append(([str(c) for c in cmd], str(cwd), (cwd / "data.txt").is_file()))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(enhanced_test_runner.subprocess, "run", _spy)
    path = _cwd_fixture(tmp_path, '# RUN_IN_FIXTURE_DIR\n'
                                  '# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n')
    assert _run(path).total_success
    (cmd, cwd, holds_the_data), = seen
    assert cmd[1] == "test_in_its_dir.sushi", cmd
    assert holds_the_data, f"{cwd} is not a copy of the fixture's directory"
    assert Path(cwd) != path.parent, "the runner compiles in a copy, never in the tree"
    assert "--cache-dir" not in cmd, "the compiler picks its own cache, as for a user"


# -- a library and a stdlib module in the fixture's directory -------------------

def _module_fixture(tmp_path: Path, header: str, use: str, dep: str) -> Path:
    home = tmp_path / "module_fixture"
    home.mkdir()
    (home / dep).write_text(DEP_V1, encoding="utf-8")
    path = home / "test_module.sushi"
    path.write_text(header + f"\n{use}\n\nfn main() i32:\n    println(\"{{N}}\")\n"
                    "    return Result.Ok(0)\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("header, passes", [
    ('# BUILD_LIB: dep.sushi\n# EXPECT_STDOUT_EXACT: "1\\n"\n', True),
    ('# EXPECT_STDOUT_EXACT: "1\\n"\n', False),
], ids=["with the directive", "without it"])
def test_build_lib_builds_a_source_library_the_fixture_imports(tmp_path, header, passes):
    result = _run(_module_fixture(tmp_path, header, "use <lib/dep>", "dep.sushi"))
    assert result.total_success is passes, _detail(result)


@pytest.mark.parametrize("header, passes", [
    ('# STDLIB_MODULE: fixture/dep=dep.sushi\n# EXPECT_STDOUT_EXACT: "1\\n"\n', True),
    ('# EXPECT_STDOUT_EXACT: "1\\n"\n', False),
], ids=["with the directive", "without it"])
def test_stdlib_module_registers_a_module_the_fixture_imports(tmp_path, header, passes):
    result = _run(_module_fixture(tmp_path, header, "use <fixture/dep>", "dep.sushi"))
    assert result.total_success is passes, _detail(result)


def test_a_library_that_does_not_build_fails_the_fixture(tmp_path):
    path = _module_fixture(tmp_path, '# BUILD_LIB: dep.sushi\n# EXPECT_STDOUT_EXACT: "1\\n"\n',
                           "use <lib/dep>", "dep.sushi")
    (path.parent / "dep.sushi").write_text("public const i32 N = \n", encoding="utf-8")
    result = _run(path)
    assert not result.total_success
    assert "BUILD_LIB dep.sushi failed" in result.compilation_message, result.compilation_message
