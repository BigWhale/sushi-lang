"""The helper libraries are built in parallel, and only the ones the selection needs (#1047).

The front end built all the helpers under `tests/libs/helpers/` one after another before
every run, also for a selection that imports none of them. These tests put a fake
`sushic` in a temporary project root, so that they read what the helper build does and
never start the compiler.
"""
from __future__ import annotations

import stat
import sys
import textwrap
from pathlib import Path

from _harness import TESTS_DIR
import run_tests
from test_metadata import select_fixtures

# The fake compiler writes `<name>.start` in the log directory, then waits until every
# name in RENDEZVOUS has started. A serial build cannot meet that rendezvous: the first
# compilation waits alone, times out and fails.
FAKE_SUSHIC = textwrap.dedent("""\
    #!{python}
    import os, sys, time
    from pathlib import Path
    log = Path({log!r})
    source = Path(sys.argv[sys.argv.index("--lib-kind") + 2])
    name = source.stem
    (log / (name + ".start")).write_text("")
    if name == "broken_lib":
        print("error: broken_lib does not compile", file=sys.stderr)
        sys.exit(2)
    wait_for = [n for n in os.environ.get("RENDEZVOUS", "").split(",") if n]
    deadline = time.monotonic() + 10
    while not all((log / (n + ".start")).exists() for n in wait_for):
        if time.monotonic() > deadline:
            print("error: no parallel build", file=sys.stderr)
            sys.exit(2)
        time.sleep(0.05)
    out = Path(sys.argv[sys.argv.index("-o") + 1])
    out.write_text(name)
    """)


def _project(tmp_path: Path, helpers: list[str]) -> tuple[Path, Path]:
    root = tmp_path / "root"
    log = tmp_path / "log"
    log.mkdir()
    helpers_dir = root / "tests" / "libs" / "helpers"
    helpers_dir.mkdir(parents=True)
    for name in helpers:
        (helpers_dir / f"{name}.sushi").write_text("public fn f() ~:\n"
                                                   "    return Result.Ok(~)\n")
    sushic = root / "sushic"
    sushic.write_text(FAKE_SUSHIC.format(python=sys.executable, log=str(log)))
    sushic.chmod(sushic.stat().st_mode | stat.S_IXUSR)
    return root, log


def _started(log: Path) -> set[str]:
    return {p.stem for p in log.glob("*.start")}


def _fixture(root: Path, rel: str, text: str) -> Path:
    path = root / "tests" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_a_selection_with_no_helper_need_builds_no_helper(tmp_path):
    root, log = _project(tmp_path, ["alpha_lib", "beta_lib"])
    fixture = _fixture(root, "io/test_plain.sushi", "fn main() i32:\n    return Result.Ok(0)\n")

    needed = run_tests.helpers_for_selection(root / "tests", [fixture])

    assert needed == set()
    assert run_tests.build_test_helpers(root, jobs=2, only=needed)
    assert _started(log) == set(), "no helper is compiled for this selection"


def test_a_selection_builds_the_helpers_its_directory_imports(tmp_path):
    root, log = _project(tmp_path, ["alpha_lib", "beta_lib", "gamma_lib"])
    fixture = _fixture(root, "libs/test_uses.sushi",
                       'use <lib/alpha_lib>\nuse "helpers/unit"\n\n'
                       "fn main() i32:\n    return Result.Ok(0)\n")
    _fixture(root, "libs/helpers/unit.sushi", "public use <lib/beta_lib> \n")

    needed = run_tests.helpers_for_selection(root / "tests", [fixture])

    assert needed == {"alpha_lib", "beta_lib"}
    assert run_tests.build_test_helpers(root, jobs=2, only=needed)
    assert _started(log) == {"alpha_lib", "beta_lib"}


def test_a_selection_removes_the_helpers_it_did_not_build(tmp_path):
    root, log = _project(tmp_path, ["alpha_lib", "beta_lib"])
    bin_dir = root / "tests" / "libs" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "beta_lib.slib").write_text("stale")

    assert run_tests.build_test_helpers(root, jobs=2, only={"alpha_lib"})
    assert (bin_dir / "alpha_lib.slib").exists()
    assert not (bin_dir / "beta_lib.slib").exists(), \
        "a helper from an earlier run must not answer for this selection"


def test_an_import_it_cannot_follow_builds_every_helper(tmp_path):
    root, _ = _project(tmp_path, ["alpha_lib"])
    fixture = _fixture(root, "deep/test_up.sushi",
                       'use "../elsewhere/unit"\n\nfn main() i32:\n    return Result.Ok(0)\n')

    assert run_tests.helpers_for_selection(root / "tests", [fixture]) is None


def test_the_helpers_build_in_parallel(tmp_path, monkeypatch):
    names = ["alpha_lib", "beta_lib", "gamma_lib"]
    root, log = _project(tmp_path, names)
    monkeypatch.setenv("RENDEZVOUS", ",".join(names))

    assert run_tests.build_test_helpers(root, jobs=len(names))
    assert _started(log) == set(names)


def test_a_helper_build_failure_fails_the_build_and_names_the_helper(tmp_path, capsys):
    root, _ = _project(tmp_path, ["alpha_lib", "broken_lib", "gamma_lib"])

    assert not run_tests.build_test_helpers(root, jobs=3)
    out = capsys.readouterr().out
    assert "broken_lib" in out and "does not compile" in out


def test_the_real_corpus_reads_a_need_for_every_selection():
    tests_dir = TESTS_DIR
    io_fixtures = select_fixtures(tests_dir, filter_pattern="io/file_read_write/test_file")
    assert io_fixtures, "control: the io selection is not empty"
    assert run_tests.helpers_for_selection(tests_dir, io_fixtures) == set()

    reexport = select_fixtures(
        tests_dir, filter_pattern="namespaces/reexport/test_run_public_use_of_a_library")
    assert run_tests.helpers_for_selection(tests_dir, reexport) == {"runtime_lib"}

    whole = run_tests.helpers_for_selection(tests_dir, select_fixtures(tests_dir))
    helpers = {p.stem for p in run_tests.helper_sources(tests_dir / "libs" / "helpers")}
    assert whole == helpers, "control: the whole corpus reaches every helper"
