"""CE2095 must point AT the declaration, not just name it."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from sushic_path import SUSHIC

LOCATED_HEAD_RE = re.compile(
    r"^(?P<file>\S+):(?P<line>\d+):(?P<col>\d+): error \[CE2095\]: ", re.MULTILINE
)

SELF_REFERENTIAL = """\
struct S:
    i32 value
    S inner

fn main() i32:
    return Result.Ok(0)
"""

MUTUAL = """\
struct A:
    i32 value
    B b

struct B:
    i32 value
    A a

fn main() i32:
    return Result.Ok(0)
"""


def _compile(tmp_path: Path, source: str, name: str = "infinite.sushi") -> str:
    (tmp_path / name).write_text(source, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    return result.stderr


def test_carries_a_source_location(tmp_path):
    stderr = _compile(tmp_path, SELF_REFERENTIAL)

    match = LOCATED_HEAD_RE.search(stderr)
    assert match is not None, f"CE2095 rendered without a location:\n{stderr}"
    assert match.group("file").endswith("infinite.sushi")
    assert match.group("line") == "1", "should point at the struct declaration"


def test_carries_caret_art(tmp_path):
    """Same shape the ladder's tier-2 rung asserts: source line, then underline."""
    stderr = _compile(tmp_path, SELF_REFERENTIAL)

    lines = stderr.splitlines()
    head = next(ln for ln in lines if "[CE2095]" in ln)
    body = lines[lines.index(head) + 1:]

    assert body[0].startswith("  |"), f"must show the source line:\n{stderr}"
    assert body[1].startswith("  `"), f"must underline the span:\n{stderr}"


def test_names_the_whole_cycle(tmp_path):
    """A one-hop cycle names one type; a two-hop cycle must name both, or the reader cannot tell
    which edge to break.
    """
    stderr = _compile(tmp_path, MUTUAL)

    assert "A refers to B refers to A" in stderr, stderr


def test_reports_each_cycle_once(tmp_path):
    """Both A and B are roots into the same cycle. Reporting per-root would print the same defect
    twice.
    """
    stderr = _compile(tmp_path, MUTUAL)

    assert stderr.count("[CE2095]") == 1, stderr
