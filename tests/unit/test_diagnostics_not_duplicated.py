"""A diagnostic is reported ONCE, however deep in an expression it occurs.

#201: every diagnostic raised inside the RECEIVER of a method call was emitted twice, because
Pass 2 validated the receiver expression two times -- once on the way to inferring its type, and
again as part of validating the call. Errors, not just warnings.

The `.sushi` harness cannot catch this: `EXPECT_ERROR_CODE` / `EXPECT_STDERR_CONTAINS` are
substring assertions, and a duplicate contains the substring just as well as a single one does.
Counting the head lines is the only oracle that sees it -- which is why the bug survived.

This is the same family as #199, one layer up: a validator walks the receiver, decides the call
is not its business, and hands it to another that walks the receiver again.
"""
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
    stderr = _compile(tmp_path, """fn mk() Maybe<i32>:
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
