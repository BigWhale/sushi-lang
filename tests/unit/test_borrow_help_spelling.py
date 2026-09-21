"""A borrow the help tells the user to write must be a borrow the parser accepts.

The `&` spelling was retired when borrow-by-default landed: an argument is written
`peek x` / `poke x`, and `&peek x` is CE6001 at the parser (#744). Three user-visible
sites still rendered the retired form.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from sushic_path import SUSHIC

#: The retired spelling, in any rendered head, note or help.
_RETIRED = re.compile(r"&\s*(peek|poke)\b")


def _compile(tmp_path: Path, source: str, name: str = "spell.sushi") -> str:
    (tmp_path / name).write_text(source, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    return result.stderr


def test_call_site_borrow_help_is_written_without_an_ampersand(tmp_path):
    """CE2006 on a `peek` parameter: the help is the repair the user types."""
    stderr = _compile(tmp_path, """fn look(peek string s) i32:
    return Result.Ok(s.len())

fn main() i32:
    let string t = "hi"
    let i32 r = look(t)??
    return Result.Ok(r)
""")
    assert "help: borrow it at the call site: `peek t`" in stderr, stderr
    assert not _RETIRED.search(stderr), stderr


def test_call_site_borrow_help_says_poke_for_a_write_borrow(tmp_path):
    """The same help for the write borrow, so the mode is carried and not hard-coded."""
    stderr = _compile(tmp_path, """fn bump(poke i32 n) ~:
    n := n + 1
    return Result.Ok(~)

fn main() i32:
    let i32 c = 0
    bump(c)
    return Result.Ok(0)
""")
    assert "help: borrow it at the call site: `poke c`" in stderr, stderr
    assert not _RETIRED.search(stderr), stderr


def test_function_value_call_help_is_written_without_an_ampersand(tmp_path):
    """CE2092, the call through a function VALUE, carries the second copy of the help."""
    stderr = _compile(tmp_path, """fn look(peek string s) i32:
    return Result.Ok(s.len())

fn main() i32:
    let fn(peek string) -> i32 f = look
    let string t = "hi"
    let i32 r = f(t)??
    return Result.Ok(r)
""")
    assert "the argument is written `peek t`" in stderr, stderr
    assert not _RETIRED.search(stderr), stderr


def test_consumed_borrow_note_is_written_without_an_ampersand(tmp_path):
    """CE2411's note names the declaration's mode, and the mode is spelled bare."""
    stderr = _compile(tmp_path, """fn eat(nom string s) ~:
    println(s)
    return Result.Ok(~)

fn look(peek string s) ~:
    eat(nom s)
    return Result.Ok(~)

fn main() i32:
    return Result.Ok(0)
""")
    assert "declared here as a `peek` borrow of the caller's value" in stderr, stderr
    assert not _RETIRED.search(stderr), stderr
