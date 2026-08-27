"""A diagnostic is reported ONCE, however deep in an expression it occurs."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def _compile(tmp_path: Path, source: str, name: str = "dup.sushi") -> str:
    (tmp_path / name).write_text(source, encoding="utf-8")
    result = subprocess.run(
        ["sushic", name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    return result.stderr


def _count(stderr: str, code: str) -> int:
    """How many diagnostic HEAD lines carry `code` (notes/help lines are not heads)."""
    return len(re.findall(rf"(?:error|warning) \[{code}\]:", stderr))


def test_error_in_method_call_receiver_reported_once(tmp_path):
    """The bug: an undefined function inside a receiver produced CE2008 twice."""
    stderr = _compile(tmp_path, """fn probe() ~:
    let bool v = undefined_fn().is_some()
    println("v = {v}")
    return Result.Ok(~)

fn main() i32:
    probe()
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2008") == 1, stderr


def test_warning_in_method_call_receiver_reported_once(tmp_path):
    """The same duplication on the warning path (CW2511, `??` in main)."""
    stderr = _compile(tmp_path, """fn mk() Maybe@(i32):
    return Result.Ok(Maybe.Some(1))

fn main() i32:
    let i32 v = mk()??.realise(0)
    println("v = {v}")
    return Result.Ok(0)
""")
    assert _count(stderr, "CW2511") == 1, stderr


def test_error_outside_a_receiver_still_reported_once(tmp_path):
    """Control: the same error NOT in a receiver position was always reported once."""
    stderr = _compile(tmp_path, """fn main() i32:
    let i32 v = undefined_fn()
    println("v = {v}")
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2008") == 1, stderr


def test_perk_impl_target_type_reported_once(tmp_path):
    """A perk implementation checked its target type once per METHOD, so N methods with
    a bad target gave N identical diagnostics. The check belongs to the header."""
    stderr = _compile(tmp_path, """perk Loud:
    fn shout() i32
    fn whisper() i32

extend Nope with Loud:
    fn shout() i32:
        return 1
    fn whisper() i32:
        return 2

fn main() i32:
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2001") == 1, stderr
