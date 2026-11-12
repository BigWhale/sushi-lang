"""A helper library that does not build is reported, and the run goes on.

The front end builds the helper libraries under `tests/libs/helpers/` before the run. One
helper that did not build aborted the whole run, so every fixture went unchecked, those
that never import the helper too. Now each helper that does not build is named with its
build message, its stale `.slib` is removed, and the run goes on: a fixture that needs
the helper fails on its own, and its message carries the build message. This adds a
report and weakens no check.

The helper build starts `sushic` directly, not through the compiler stub of the fork
server. So the first two tests put a fake `sushic` in a temporary project root (the model
of `test_helper_build_scales_with_the_selection.py`), and the front-end test stages a
helper with a parse error, which no compiler builds.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from _harness import TESTS_DIR, run_tests
import run_tests as front_end

FAKE_SUSHIC = textwrap.dedent("""\
    #!{python}
    import sys
    from pathlib import Path
    name = Path(sys.argv[sys.argv.index("--lib-kind") + 2]).stem
    if name == "broken_lib":
        print("error: broken_lib does not compile", file=sys.stderr)
        sys.exit(2)
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(name)
    """)


def _project(tmp_path: Path, helpers: list[str]) -> Path:
    root = tmp_path / "root"
    helpers_dir = root / "tests" / "libs" / "helpers"
    helpers_dir.mkdir(parents=True)
    for name in helpers:
        (helpers_dir / f"{name}.sushi").write_text("public fn f() ~:\n    return ~\n")
    sushic = root / "sushic"
    sushic.write_text(FAKE_SUSHIC.format(python=sys.executable))
    sushic.chmod(sushic.stat().st_mode | stat.S_IXUSR)
    return root


def test_the_build_names_each_failed_helper_with_its_message(tmp_path):
    root = _project(tmp_path, ["alpha_lib", "broken_lib"])
    built = front_end.build_test_helpers(root, jobs=2)
    assert not built
    assert list(built.failures) == ["broken_lib"]
    assert "does not compile" in built.failures["broken_lib"]


def test_a_failed_helper_leaves_no_stale_library(tmp_path):
    root = _project(tmp_path, ["broken_lib"])
    stale = root / "tests" / "libs" / "bin" / "broken_lib.slib"
    stale.parent.mkdir(parents=True)
    stale.write_text("an earlier build")
    assert not front_end.build_test_helpers(root, jobs=1)
    assert not stale.exists(), "a library of an earlier build answers for a failed one"


@pytest.fixture
def staged():
    """A broken helper, and a fixture directory with one fixture that imports it and one
    that does not. Both fixtures expect an error that comes before any body is checked."""
    marker = f"failed_helper_tmp_{os.getpid()}"
    helper = TESTS_DIR / "libs" / "helpers" / f"{marker}_lib.sushi"
    helper.write_text("public fn f( i32:\n    return 0\n", encoding="utf-8")
    directory = TESTS_DIR / marker
    directory.mkdir()
    (directory / "test_err_plain.sushi").write_text(
        "# EXPECT_ERROR_CODE: CE1001\n\nfn main() i32:\n    return nope\n", encoding="utf-8")
    (directory / "test_err_needs_helper.sushi").write_text(
        f"# EXPECT_ERROR_CODE: CE1001\n\nuse <lib/{marker}_lib>\n\n"
        "fn main() i32:\n    return nope\n", encoding="utf-8")
    try:
        yield marker
    finally:
        helper.unlink(missing_ok=True)
        (TESTS_DIR / "libs" / "bin" / f"{marker}_lib.slib").unlink(missing_ok=True)
        shutil.rmtree(directory, ignore_errors=True)


def test_the_run_goes_on_and_the_fixture_that_needs_the_helper_fails(staged):
    done = run_tests("--json", "--filter", staged)
    assert "aborting tests" not in done.stdout + done.stderr, done.stderr[-3000:]
    report = json.loads(done.stdout)
    assert (report["passed"], report["failed"]) == (1, 1), done.stdout[-3000:]
    [failed] = report["failed_tests"]
    assert failed["name"].endswith("test_err_needs_helper.sushi")
    assert f"{staged}_lib" in failed["message"], failed["message"]
    assert "CE6001" in failed["message"], "the build message of the helper is missing"
    assert done.returncode == 1
