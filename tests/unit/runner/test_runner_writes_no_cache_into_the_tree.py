"""A test run writes no compiler cache into the source tree (#1044).

A fixture with no workspace of its own compiled with no `--cache-dir`, so the compiler
put its cache beside the source, in `tests/<dir>/__sushi_cache__/`, shared by every
fixture of that directory. The front end then deleted every such directory before each
run. The cache key folds in a digest of the compiler source, so the key is the
correctness mechanism; the runner gives every compilation a cache in the run's own
temporary directory and does not purge.
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = TESTS_DIR.parent

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
import run_tests  # noqa: E402

PROGRAM = (
    '# EXPECT_STDOUT_EXACT: "{word}\\n"\n'
    "\n"
    'use "{unit}"\n'
    "\n"
    "fn main() i32:\n"
    "    say()\n"
    "    return Result.Ok(0)\n"
)

UNIT = (
    "public fn say() ~:\n"
    '    println("{word}")\n'
    "    return Result.Ok(~)\n"
)


def _multi_unit_fixtures(root: Path) -> list[Path]:
    """Two programs of two units each, in two directories: each one writes a cache."""
    fixtures = []
    for directory, word in (("left", "alpha"), ("right", "beta")):
        home = root / directory
        home.mkdir(parents=True)
        unit = f"cache_probe_{directory}"
        (home / f"{unit}.sushi").write_text(UNIT.format(word=word), encoding="utf-8")
        source = home / f"test_cache_probe_{directory}.sushi"
        source.write_text(PROGRAM.format(word=word, unit=unit), encoding="utf-8")
        fixtures.append(source)
    return fixtures


def test_a_multi_unit_fixture_leaves_no_cache_beside_its_source(tmp_path):
    fixtures = _multi_unit_fixtures(tmp_path)

    with enhanced_test_runner.TestRunner(tmp_path, project_root=PROJECT_ROOT,
                                         parallel_jobs=2) as runner:
        results = runner.run_all_tests()
        run_dir = Path(runner.temp_dir)

    assert len(results) == len(fixtures)
    failed = {name: (r.compilation_message, r.runtime_message)
              for name, r in results.items() if not r.total_success}
    assert not failed, failed
    left_behind = sorted(str(p.relative_to(tmp_path))
                         for p in tmp_path.rglob("__sushi_cache__"))
    assert not left_behind, f"the run wrote a cache into the fixture tree: {left_behind}"
    assert not run_dir.exists(), "the run's own directory, its cache with it, outlived the run"


def test_the_front_end_purges_no_cache():
    """The cache key is the correctness mechanism; no runner repeats it with a purge."""
    for module in (run_tests, enhanced_test_runner):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "purge_unit_caches" not in source, (
            f"{Path(module.__file__).name} still deletes the compiler caches before a run")
