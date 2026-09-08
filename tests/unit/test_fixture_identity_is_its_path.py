"""A fixture's identity is its path under tests/, not its file name (issue #604).

Both runners named the output binary after the file's stem and the process id. The
runners use THREADS in one process, so the process id is constant and the key is the
stem alone. Four stems collided; two tests shared one binary and both reported a pass,
and a leak assertion could run against the wrong program.

Two rules keep that shut. The binary name carries the fixture's DIRECTORY, so a
collision is impossible whatever the corpus holds; and no two fixtures share a file
name, so every by-name key the harness still uses -- the results map, the quarantine
sets, the printed report -- names exactly one test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_DIR.parent

# tests/ is not a package; the harness modules import each other flat.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
import run_tests  # noqa: E402
from test_metadata import (  # noqa: E402
    collect_fixtures,
    fixture_binary_name,
    fixture_id,
    parse_test_metadata,
)

PRINTER = (
    '# EXPECT_STDOUT_EXACT: "{word}\\n"\n'
    "\n"
    "fn main() i32:\n"
    '    println("{word}")\n'
    "    return Result.Ok(0)\n"
)


def _twin_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    """Two fixtures of ONE stem in two directories -- the shape that collided."""
    pair = []
    for directory, word in (("left", "alpha"), ("right", "beta")):
        home = tmp_path / directory
        home.mkdir()
        source = home / "test_twin.sushi"
        source.write_text(PRINTER.format(word=word), encoding="utf-8")
        pair.append(source)
    return pair[0], pair[1]


def test_no_two_fixtures_share_a_file_name():
    """The gate: a duplicate stem cannot come back.

    Every by-name key in the harness -- the results map keyed by `test_file.name`, the
    two quarantine sets, the line the report prints -- assumes the name picks out one
    test. A duplicate silently drops a row from the count and makes a failure
    unattributable.
    """
    seen: dict[str, list[str]] = {}
    for fixture in collect_fixtures(TESTS_DIR):
        seen.setdefault(fixture.name, []).append(
            str(fixture.relative_to(TESTS_DIR)))

    duplicates = {name: paths for name, paths in seen.items() if len(paths) > 1}
    assert not duplicates, (
        "these fixture names name more than one test:\n"
        + "\n".join(f"  {name}\n" + "\n".join(f"    {p}" for p in sorted(paths))
                    for name, paths in sorted(duplicates.items()))
    )


def test_the_identity_of_a_fixture_is_its_path():
    """`fixture_id` reads the directory, so two same-stem fixtures differ."""
    left = TESTS_DIR / "left" / "test_twin.sushi"
    right = TESTS_DIR / "right" / "test_twin.sushi"

    assert fixture_id(left, TESTS_DIR) == "left/test_twin"
    assert fixture_id(right, TESTS_DIR) != fixture_id(left, TESTS_DIR)


def test_every_fixture_in_the_corpus_gets_its_own_binary_name():
    """One name per fixture, over the corpus the runners actually collect."""
    seen: dict[str, list[str]] = {}
    for fixture in collect_fixtures(TESTS_DIR):
        seen.setdefault(fixture_binary_name(fixture, TESTS_DIR), []).append(
            str(fixture.relative_to(TESTS_DIR)))

    collisions = {name: paths for name, paths in seen.items() if len(paths) > 1}
    assert not collisions, f"these fixtures share one binary name: {collisions}"


def test_a_fixture_outside_the_corpus_still_gets_its_own_name(tmp_path):
    """A gate builds a fixture in a temp directory; it has no path under tests/.

    The runners are handed such a file directly, and two of them may carry one stem,
    so the fallback has to separate them as well.
    """
    left, right = _twin_fixtures(tmp_path)

    assert fixture_binary_name(left, TESTS_DIR) != fixture_binary_name(right, TESTS_DIR)


def test_two_fixtures_of_one_stem_do_not_share_a_binary(tmp_path):
    """The reproduction: compile both, and both programs must survive.

    The enhanced runner compiles into one temporary directory. With the stem as the
    name, the second compilation overwrote the first binary and both tests then ran
    whichever program won -- so both passed on ONE program's output.
    """
    left, right = _twin_fixtures(tmp_path)

    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        for source in (left, right):
            ok, message = runner._run_compilation_test(
                source, "success", parse_test_metadata(source))
            assert ok, message

        binaries = sorted(p for p in Path(runner.temp_dir).iterdir() if p.is_file())
        assert len(binaries) == 2, (
            "the two fixtures compiled to one binary: "
            f"{[p.name for p in binaries]}"
        )

        printed = set()
        for binary in binaries:
            binary.chmod(0o755)
            printed.add(subprocess.run([str(binary)], capture_output=True, text=True,
                                       timeout=60).stdout)
    assert printed == {"alpha\n", "beta\n"}, (
        f"the binaries do not print what their own sources print: {printed}")


def test_the_compile_only_runner_keeps_them_apart(tmp_path):
    """`run_tests.py` names its output the same way, into a directory it reuses."""
    left, right = _twin_fixtures(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    for source in (left, right):
        _name, passed, expected, actual, output = run_tests.run_single_test(
            source, bin_dir, TESTS_DIR)
        assert passed, f"expected exit {expected}, got {actual}\n{output}"

    produced = sorted(p.name for p in bin_dir.iterdir())
    assert len(produced) == 2, f"the two fixtures compiled to one binary: {produced}"


@pytest.mark.parametrize("module", [enhanced_test_runner, run_tests])
def test_no_runner_names_a_binary_from_the_stem(module):
    """The seam, held: neither runner rebuilds the name it was handed."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "test_file.stem" not in source, (
        f"{Path(module.__file__).name} still derives an output name from the stem; "
        "the collision this closes comes straight back"
    )


@pytest.mark.parametrize("module", [enhanced_test_runner, run_tests])
def test_the_corpus_is_collected_in_one_place(module):
    """One collector, so the gate above scans exactly what the runners run."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert 'rglob("test_*.sushi")' not in source, (
        f"{Path(module.__file__).name} globs the corpus itself; a fixture it collects "
        "and collect_fixtures does not is a test no gate sees"
    )
