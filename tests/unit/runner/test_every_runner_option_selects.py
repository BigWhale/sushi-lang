"""Every option of the runner SELECTS fixtures; no option makes a check weaker (#1045).

`--mode compile` skipped the runtime step of every fixture, so each success fixture was
checked for its compile alone. A flag may narrow WHICH fixtures run, never how hard they
are checked. The second half: a fixture's identity is its path under `tests/`, so the
runner keys its results and its workspaces by that path and not by the file name.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = TESTS_DIR.parent

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
import run_tests  # noqa: E402

# The options that narrow the list of fixtures a run holds.
SELECTS = {"--filter", "--leaks-only", "--compile-only"}
# The options that change how a run is shown or scheduled, and nothing it asserts.
REPORTS = {"-h", "--help", "-v", "--verbose", "-j", "--jobs", "--json"}
# `--skip-build` skips the builds a run needs; it does not skip a check.
PREPARES = {"--skip-build"}
# `--enhanced` is accepted and does nothing: every run is the enhanced run.
NO_EFFECT = {"--enhanced"}
# The one escape, and the only one: a platform with no interposer cannot check a leak,
# and a skip fails the run without it (#605).
ESCAPES = {"--allow-leak-skips"}

KNOWN = SELECTS | REPORTS | PREPARES | NO_EFFECT | ESCAPES


def _options(parser) -> set[str]:
    return {flag for action in parser._actions for flag in action.option_strings}


@pytest.mark.parametrize("module", [enhanced_test_runner, run_tests],
                         ids=["enhanced_test_runner", "run_tests"])
def test_every_option_is_a_known_kind(module):
    """A new option must be named here with its kind, so a weaker mode cannot slip in."""
    unknown = _options(module.build_parser()) - KNOWN
    assert not unknown, (
        f"{Path(module.__file__).name} offers options of no known kind: {sorted(unknown)}; "
        "an option selects fixtures or shapes the report, and never weakens a check")


def test_the_runner_offers_no_mode():
    done = subprocess.run([sys.executable, str(TESTS_DIR / "enhanced_test_runner.py"),
                           "--help"], capture_output=True, text=True, timeout=60,
                          cwd=PROJECT_ROOT)
    assert done.returncode == 0, done.stderr
    assert "--mode" not in done.stdout, done.stdout
    assert "--compile-only" in done.stdout, "the control: the help lists the options"


def test_the_runner_holds_no_mode_attribute(tmp_path):
    with enhanced_test_runner.TestRunner(tmp_path, project_root=PROJECT_ROOT) as runner:
        assert not hasattr(runner, "mode"), "a mode that weakens every check is back"


def _twin(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True)
    path = directory / "test_twin.sushi"
    path.write_text(f'# EXPECT_STDOUT_EXACT: "{text}\\n"\n# RUN_IN_FIXTURE_DIR\n\n'
                    f'fn main() i32:\n    println("{text}")\n    return Result.Ok(0)\n',
                    encoding="utf-8")
    return path


def test_results_are_keyed_by_the_path_under_tests(tmp_path):
    """Two fixtures of one file name are two results; the name alone kept one of them."""
    _twin(tmp_path / "left", "alpha")
    _twin(tmp_path / "right", "beta")
    with enhanced_test_runner.TestRunner(tmp_path, project_root=PROJECT_ROOT,
                                         parallel_jobs=1) as runner:
        results = runner.run_all_tests()
    assert sorted(results) == ["left/test_twin.sushi", "right/test_twin.sushi"], results
    assert all(r.total_success for r in results.values()), {
        key: (r.compilation_message, r.runtime_message) for key, r in results.items()}


def test_no_dictionary_is_keyed_by_the_file_name():
    """The seam, held: every key of `results` and `_workspaces` is the fixture's identity."""
    tree = ast.parse(Path(enhanced_test_runner.__file__).read_text(encoding="utf-8"))
    keyed_by_name = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            target, key = node.value, node.slice
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in ("get", "pop") and node.args):
            target, key = node.func.value, node.args[0]
        else:
            continue
        name = ast.unparse(target)
        if (name in ("results", "self._workspaces")
                and ast.unparse(key) not in ("key", "self._key(test_file)")):
            keyed_by_name.append(f"line {node.lineno}: {ast.unparse(node)}")
    assert not keyed_by_name, keyed_by_name
