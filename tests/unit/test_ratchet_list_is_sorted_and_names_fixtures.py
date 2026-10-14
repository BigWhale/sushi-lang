"""The ratchet list `tests/ir/ratchet.txt` is sorted, and each line names a fixture.

The runner reads the list with `--list`. Each line is one repository-relative path, in the
form `tests/<area>/.../test_<name>.sushi`. A line must name a file that exists and that
`collect_fixtures` collects, so that the list cannot name a helper unit or a deleted
fixture. The lines are in string order, and no line occurs two times.

This gate does not require the whole corpus: from the switch to SHIR the list is empty and
only grows. The gate reads files and never starts the compiler.
"""
from __future__ import annotations

import sys
from collections import Counter
from itertools import pairwise
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_metadata import collect_fixtures, read_fixture_list  # noqa: E402

RATCHET = TESTS / "ir" / "ratchet.txt"


def _lines() -> list[str]:
    return RATCHET.read_text(encoding="utf-8").splitlines()


def test_the_ratchet_list_exists():
    assert RATCHET.is_file(), f"{RATCHET.relative_to(ROOT)} is missing"


def test_each_line_is_a_repository_relative_path():
    bad = []
    for number, line in enumerate(_lines(), start=1):
        path = PurePosixPath(line)
        if (not line or line != line.strip() or "\\" in line or path.is_absolute()
                or ".." in path.parts or path.parts[0] != "tests"
                or path.suffix != ".sushi"):
            bad.append(f"line {number}: {line!r}")
    assert not bad, "lines that are not a repository-relative fixture path:\n" + "\n".join(bad)


def test_the_runner_reads_every_line_as_a_path():
    assert read_fixture_list(RATCHET) == _lines()


def test_each_line_names_a_collected_fixture():
    known = {f.relative_to(ROOT).as_posix() for f in collect_fixtures(TESTS)}
    missing = [line for line in _lines() if not (ROOT / line).is_file()]
    assert not missing, "lines that name no file:\n" + "\n".join(missing)
    unknown = [line for line in _lines() if line not in known]
    assert not unknown, "lines that name no collected fixture:\n" + "\n".join(unknown)


def test_the_lines_are_sorted_and_unique():
    lines = _lines()
    repeated = sorted(line for line, count in Counter(lines).items() if count > 1)
    assert not repeated, "lines that occur more than one time:\n" + "\n".join(repeated)
    out_of_order = [f"{a} > {b}" for a, b in pairwise(lines) if a > b]
    assert not out_of_order, "lines out of string order:\n" + "\n".join(out_of_order)


def test_the_file_ends_with_a_newline():
    text = RATCHET.read_text(encoding="utf-8")
    assert not text or text.endswith("\n"), "the last line has no newline"
