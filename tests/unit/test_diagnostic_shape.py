"""Every diagnostic follows the same patterns -- one test per rung of the ladder.

"The same structure" does not mean one rigid layout. Diagnostics differ in how
much location evidence they carry, and the ladder is:

    tier 1  text only                      -- no meaningful source position
    tier 2  text + one primary location    -- the common case
    tier 3  tier 2 + secondary locations   -- a relational error ("this conflicts
                                              with that"), each note carrying its
                                              own file:line:col and its own snippet

A single golden test of one error would prove nothing about the ladder, so there
is one per tier. They assert the SKELETON -- severity word, [CE####] token,
presence or absence of a location, gutter and caret, the note's independent
location block -- not the prose. It is a structural contract, not a message tax.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path



HEAD_RE = re.compile(r"^(?P<loc>\S*?):?\s*(?P<severity>error|warning) \[(?P<code>[A-Z]{2}\d{4})\]: (?P<message>.+)$")
LOCATED_HEAD_RE = re.compile(r"^(?P<file>\S+):(?P<line>\d+):(?P<col>\d+): (?P<severity>error|warning) \[(?P<code>[A-Z]{2}\d{4})\]: ")


def _compile(tmp_path: Path, source: str, name: str = "shape.sushi") -> str:
    import os

    (tmp_path / name).write_text(source, encoding="utf-8")
    result = subprocess.run(
        ["sushic", name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    return result.stderr


def test_tier1_text_only(tmp_path):
    """No source position: the head line, and nothing under it."""
    stderr = _compile(tmp_path, "fn main() i32:\n    return Result.Ok(0)")  # no trailing newline

    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    head = next(ln for ln in lines if "[CW0001]" in ln)

    assert "warning [CW0001]" in head
    assert head.startswith("./shape.sushi:") or head.startswith("shape.sushi:")
    # A tier-1 diagnostic has no line:col and draws no snippet.
    assert not LOCATED_HEAD_RE.match(head)
    assert not any(ln.startswith("  |") or ln.startswith("  `") for ln in lines)


def test_tier2_one_location(tmp_path):
    """A local, self-contained error: head with file:line:col, source line, caret."""
    stderr = _compile(tmp_path, "fn main() i32:\n    let i32 x = \n    return Result.Ok(0)\n")

    lines = stderr.splitlines()
    head = next(ln for ln in lines if "[CE6001]" in ln)
    match = LOCATED_HEAD_RE.match(head)

    assert match, f"tier-2 head must carry file:line:col -- got {head!r}"
    assert match.group("severity") == "error"
    assert int(match.group("line")) == 2

    body = lines[lines.index(head) + 1:]
    assert body[0].startswith("  |"), "tier 2 must show the source line"
    assert body[1].startswith("  `"), "tier 2 must underline the span"
    # No secondary location.
    assert not any(ln.lstrip().startswith("= note:") and "shape.sushi" in ln for ln in body)


def test_tier3_two_locations(tmp_path):
    """A relational error: everything tier 2 has, plus a note with its OWN location."""
    source = (
        "fn twice() i32:\n"
        "    return Result.Ok(1)\n"
        "\n"
        "fn twice() i32:\n"
        "    return Result.Ok(2)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    stderr = _compile(tmp_path, source)

    lines = stderr.splitlines()
    head = next(ln for ln in lines if "[CE0101]" in ln)
    match = LOCATED_HEAD_RE.match(head)

    assert match, f"tier-3 head must carry file:line:col -- got {head!r}"
    assert int(match.group("line")) == 4, "primary location is the DUPLICATE"

    body = lines[lines.index(head) + 1:]
    assert body[0].startswith("  |")
    assert body[1].startswith("  `")

    note_idx = next(i for i, ln in enumerate(body) if ln.lstrip().startswith("= note:"))
    # The note carries its own location, on its own line, and its own snippet.
    note_loc = body[note_idx + 1].strip()
    assert re.match(r"^\S+:\d+:\d+$", note_loc), \
        f"a tier-3 note must carry its own file:line:col -- got {note_loc!r}"
    assert note_loc.endswith(":1:4"), "secondary location is the FIRST definition"
    assert body[note_idx + 2].lstrip().startswith("|"), "the note must show its own snippet"


def test_tier3_secondary_location_can_live_in_another_file(tmp_path):
    """A note carries its OWN filename, so a conflict can span two files.

    Reporter._get_source_lines() re-reads the other file from disk to draw its
    snippet. This capability was built and unused before 4.7.
    """
    import os

    (tmp_path / "helper.sushi").write_text(
        "public fn twice(i32 n) i32:\n    return Result.Ok(n * 2)\n", encoding="utf-8"
    )
    (tmp_path / "main.sushi").write_text(
        'use "helper"\n'
        "\n"
        "fn twice(i32 n) i32:\n"
        "    return Result.Ok(n + n)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sushic", "main.sushi"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )

    assert result.returncode == 2, result.stdout + result.stderr
    stderr = result.stderr

    # The primary location is in main.sushi; the diagnostic must also name the
    # OTHER file, with a line:col of its own.
    assert "main.sushi" in stderr
    assert re.search(r"helper\.sushi:\d+:\d+", stderr), (
        f"the secondary location must point into helper.sushi:\n{stderr}"
    )


def test_help_carries_no_location(tmp_path):
    """A help is advice, never a location -- the bottom rung of the same skeleton."""
    stderr = _compile(tmp_path, "fn main() i32:\n    if true:\n        println(\"a\")\n    return Result.Ok(0)\n")

    lines = stderr.splitlines()
    help_line = next(ln for ln in lines if ln.lstrip().startswith("= help:"))

    assert "if (condition):" in help_line
    following = lines[lines.index(help_line) + 1:]
    assert not any(re.match(r"^\s+\S+:\d+:\d+$", ln) for ln in following), \
        "a help must not be followed by a location block"
