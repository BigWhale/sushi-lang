"""A collect-pass diagnostic names the unit the declaration is in (#473).

The collect pass walks every unit through ONE reporter, unlike the per-unit passes, which
build their own. A span from a non-entry unit rendered against the entry file names a line
the user did not write, and the caret lands on whatever text sits at that column.

Every case below puts the fault in the NON-ENTRY unit and then checks the reported location
against the file it names: the line it points at has to be the declaration.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


needs_sushic = pytest.mark.skipif(shutil.which("sushic") is None,
                                  reason="sushic not on PATH")

MAIN = """\
use "helper"

fn main() i32:
    println("{touch().realise(0)}")
    return Result.Ok(0)
"""

_HEAD = re.compile(r"^\.?/?(?P<file>[\w./-]+\.sushi):(?P<line>\d+):(?P<col>\d+): "
                   r"(?:error|warning) \[(?P<code>[A-Z]{2}\d{4})\]", re.M)
_NOTE = re.compile(r"^\s+\.?/?(?P<file>[\w./-]+\.sushi):(?P<line>\d+):(?P<col>\d+)\s*$", re.M)


def _compile(tmp_path: Path, helper: str, main: str = MAIN):
    project = tmp_path / "prog"
    project.mkdir(parents=True, exist_ok=True)
    (project / "helper.sushi").write_text(helper, encoding="utf-8")
    (project / "main.sushi").write_text(main, encoding="utf-8")
    r = subprocess.run(["sushic", "main.sushi", "-o", "out"], cwd=project,
                       capture_output=True, text=True, env={**os.environ})
    return project, r, r.stdout + r.stderr


def _line_at(project: Path, file: str, line: int) -> str:
    """The source line a diagnostic points at, as the user would read it."""
    text = (project / Path(file).name).read_text(encoding="utf-8").splitlines()
    return text[line - 1] if 0 <= line - 1 < len(text) else ""


def _head(out: str, code: str) -> tuple[str, int]:
    for m in _HEAD.finditer(out):
        if m.group("code") == code:
            return m.group("file"), int(m.group("line"))
    raise AssertionError(f"no {code} head line in:\n{out}")


def _note(out: str) -> tuple[str, int]:
    m = _NOTE.search(out)
    assert m is not None, f"no located note in:\n{out}"
    return m.group("file"), int(m.group("line"))


# --- The fault is inside the non-entry unit ----------------------------------------

@needs_sushic
def test_a_duplicate_constant_names_its_own_unit(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
# pad
const i32 DUP = 1
const i32 DUP = 2

public fn touch() i32:
    return Result.Ok(DUP)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE0105")
    assert file.endswith("helper.sushi"), out
    assert "const i32 DUP" in _line_at(project, file, line)

    note_file, note_line = _note(out)
    assert note_file.endswith("helper.sushi"), out
    assert "const i32 DUP" in _line_at(project, note_file, note_line)


@needs_sushic
def test_a_duplicate_struct_names_its_own_unit(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
struct Dup:
    i32 a

struct Dup:
    i32 b

public fn touch() i32:
    return Result.Ok(1)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE0004")
    assert file.endswith("helper.sushi"), out
    assert "struct Dup" in _line_at(project, file, line)

    note_file, note_line = _note(out)
    assert "struct Dup" in _line_at(project, note_file, note_line)


@needs_sushic
def test_a_duplicate_enum_names_its_own_unit(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
enum Colour:
    Red

enum Colour:
    Blue

public fn touch() i32:
    return Result.Ok(1)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE2046")
    assert file.endswith("helper.sushi"), out
    assert "enum Colour" in _line_at(project, file, line)


@needs_sushic
def test_a_duplicate_extern_names_its_own_unit(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
unsafe external "C" as libc because "declared twice in one namespace":
    fn strlen(string s) i64 = "strlen"
    fn strlen(string s) i64 = "strlen"

public fn touch() i32:
    return Result.Ok(1)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE0101")
    assert file.endswith("helper.sushi"), out
    assert "fn strlen" in _line_at(project, file, line)

    note_file, note_line = _note(out)
    assert "fn strlen" in _line_at(project, note_file, note_line)


@needs_sushic
def test_a_duplicate_perk_names_its_own_unit(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
perk Show:
    fn show() string

perk Show:
    fn show() string

public fn touch() i32:
    return Result.Ok(1)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE4001")
    assert file.endswith("helper.sushi"), out
    assert "perk Show" in _line_at(project, file, line)

    note_file, note_line = _note(out)
    assert "perk Show" in _line_at(project, note_file, note_line)


@needs_sushic
def test_a_reserved_link_name_names_its_own_unit(tmp_path):
    # CE5001 carries no note, so the head is the whole diagnostic -- and it used to mark
    # a correct line of the consumer's own code.
    project, r, out = _compile(tmp_path, """\
# pad
unsafe external "C" as libc because "a reserved link-name, wrong signature":
    fn my_strlen(i32 x) i32 = "strlen"

public fn touch() i32:
    return Result.Ok(libc.my_strlen(3))
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE5001")
    assert file.endswith("helper.sushi"), out
    assert "my_strlen" in _line_at(project, file, line)


# --- Across two units: each half names its own -------------------------------------

@needs_sushic
def test_a_cross_unit_duplicate_locates_both_halves(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
struct Pair:
    i32 a

public fn touch() i32:
    return Result.Ok(1)
""", main="""\
use "helper"

struct Pair:
    i32 b

fn main() i32:
    println("{touch().realise(0)}")
    return Result.Ok(0)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE0004")
    assert "struct Pair" in _line_at(project, file, line), out

    note_file, note_line = _note(out)
    assert "struct Pair" in _line_at(project, note_file, note_line), out
    # One of each: the duplicate and the first declaration are in different files.
    assert {Path(file).name, Path(note_file).name} == {"main.sushi", "helper.sushi"}, out


@needs_sushic
def test_a_cross_unit_duplicate_extern_locates_both_halves(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
unsafe external "C" as libc because "the same symbol, from the other unit":
    fn strlen(string s) i64 = "strlen"

public fn touch() i32:
    return Result.Ok(1)
""", main="""\
use "helper"

unsafe external "C" as libc because "the same symbol, from the entry unit":
    fn strlen(string s) i64 = "strlen"

fn main() i32:
    println("{touch().realise(0)}")
    return Result.Ok(0)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE0101")
    assert "fn strlen" in _line_at(project, file, line), out

    note_file, note_line = _note(out)
    assert "fn strlen" in _line_at(project, note_file, note_line), out
    assert {Path(file).name, Path(note_file).name} == {"main.sushi", "helper.sushi"}, out


@needs_sushic
def test_a_cross_unit_duplicate_perk_locates_both_halves(tmp_path):
    project, r, out = _compile(tmp_path, """\
# pad
perk Show:
    fn show() string

public fn touch() i32:
    return Result.Ok(1)
""", main="""\
use "helper"

perk Show:
    fn show() string

fn main() i32:
    println("{touch().realise(0)}")
    return Result.Ok(0)
""")
    assert r.returncode == 2, out
    file, line = _head(out, "CE4001")
    assert "perk Show" in _line_at(project, file, line), out

    note_file, note_line = _note(out)
    assert "perk Show" in _line_at(project, note_file, note_line), out
    assert {Path(file).name, Path(note_file).name} == {"main.sushi", "helper.sushi"}, out


# --- What must not change -----------------------------------------------------------

@needs_sushic
def test_the_entry_unit_is_reported_as_before(tmp_path):
    project = tmp_path / "solo"
    project.mkdir()
    (project / "main.sushi").write_text("""\
struct Dup:
    i32 a

struct Dup:
    i32 b

fn main() i32:
    return Result.Ok(0)
""", encoding="utf-8")
    r = subprocess.run(["sushic", "main.sushi", "-o", "out"], cwd=project,
                       capture_output=True, text=True, env={**os.environ})
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    file, line = _head(out, "CE0004")
    assert file.endswith("main.sushi"), out
    assert "struct Dup" in _line_at(project, file, line)
