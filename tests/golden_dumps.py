"""The golden dumps of the test runner: `EXPECT_DUMP_SHIR:` and `EXPECT_DUMP_SLIR:`.

A fixture names a golden file beside it, and the runner compares a dump of the program with
that file, byte for byte (docs/design/ir.md, 11.3).

The contract with the compiler is `dump_flags`: for each dump the fixture compares, the
runner adds `<flag> <path>` to the compile, and the compiler writes the dump of the whole
program to `<path>`. The compile goes on as usual, so every other check of the fixture
still holds. `<path>` is beside the output of the compile, in the run's own directory.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import List, Optional

from test_metadata import GOLDEN_DUMPS, GoldenDump, TestMetadata

# How many lines of a difference a failure shows; it counts the rest.
DIFF_LINES_SHOWN = 40


def dump_path(binary_path: Path, dump: GoldenDump) -> Path:
    """Where the compile of a fixture writes one dump."""
    return Path(f"{binary_path}{dump.suffix}")


def dumps_of(metadata: TestMetadata) -> List[GoldenDump]:
    """The dumps a fixture compares, in the order of GOLDEN_DUMPS."""
    return [dump for dump in GOLDEN_DUMPS if dump.kind in metadata.golden_dumps]


def dump_flags(metadata: TestMetadata, binary_path: Path) -> List[str]:
    """The flags that ask the compiler for each dump the fixture compares."""
    return [part for dump in dumps_of(metadata)
            for part in (dump.flag, str(dump_path(binary_path, dump)))]


def _difference(golden: str, actual: str, name: str) -> str:
    lines = list(difflib.unified_diff(golden.splitlines(keepends=True),
                                      actual.splitlines(keepends=True),
                                      fromfile=name, tofile="the dump"))
    shown = [line.rstrip("\n") if line.endswith("\n") else f"{line} (no newline at end)"
             for line in lines[:DIFF_LINES_SHOWN]]
    more = len(lines) - DIFF_LINES_SHOWN
    return "\n    ".join(shown + ([f"... and {more} more line(s)"] if more > 0 else []))


def check_dumps(test_file: Path, metadata: TestMetadata, binary_path: Path,
                message: str) -> tuple[bool, str]:
    """Each dump the fixture names, against its golden file beside the fixture."""
    problems = []
    for dump in dumps_of(metadata):
        problem = _check_one(test_file, metadata.golden_dumps[dump.kind], dump,
                             dump_path(binary_path, dump))
        if problem is not None:
            problems.append(problem)
    if problems:
        return False, "✗ Compilation: a golden dump differs\n  " + "\n  ".join(problems)
    return True, message


def _check_one(test_file: Path, name: str, dump: GoldenDump,
               written: Path) -> Optional[str]:
    if not written.is_file():
        return f"{dump.directive}: the compiler wrote no dump at {written}"
    golden = Path(test_file).parent / name
    if not golden.is_file():
        return f"{dump.directive}: the golden file {golden} is missing"
    expected = golden.read_text(encoding="utf-8")
    actual = written.read_text(encoding="utf-8")
    if actual == expected:
        return None
    return f"{dump.directive}: the dump differs from {name}\n    " + _difference(
        expected, actual, name)
