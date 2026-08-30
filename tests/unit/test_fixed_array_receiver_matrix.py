"""The gate on #480: one address seam behind every fixed-array receiver shape.

A compile-only test passes on the bug this pins. `b.slots.fill(9)` compiled, ran, and left
the field unchanged, so each row here asserts the OBSERVED result and not the exit code.
"""
from __future__ import annotations

import subprocess

import pytest
from sushic_path import SUSHIC, needs_sushic

pytestmark = needs_sushic

# (label, receiver setup, the call, what the owner must read back afterwards)
WRITE_ROWS = [
    ("local", "let i32[3] a = [1, 2, 3]", "a.fill(9)", "a", "9 9 9"),
    ("local_reverse", "let i32[3] a = [1, 2, 3]", "a.reverse()", "a", "3 2 1"),
    ("field", "let Buf b = Buf(slots: [1, 2, 3])", "b.slots.fill(9)", "b.slots", "9 9 9"),
    ("field_reverse", "let Buf b = Buf(slots: [1, 2, 3])", "b.slots.reverse()", "b.slots", "3 2 1"),
    ("nested_field", "let Outer o = Outer(inner: Buf(slots: [1, 2, 3]))",
     "o.inner.slots.fill(9)", "o.inner.slots", "9 9 9"),
]

READ_ROWS = [
    ("local", "let i32[3] a = [1, 2, 3]", "a"),
    ("field", "let Buf b = Buf(slots: [1, 2, 3])", "b.slots"),
    ("nested_field", "let Outer o = Outer(inner: Buf(slots: [1, 2, 3]))", "o.inner.slots"),
    ("constant", "", "TRIPLE"),
]

PRELUDE = """use <io/stdio>

const i32[3] TRIPLE = [1, 2, 3]

struct Buf:
    i32[3] slots

struct Outer:
    Buf inner
"""


def _run(tmp_path, source: str) -> subprocess.CompletedProcess:
    (tmp_path / "case.sushi").write_text(source, encoding="utf-8")
    built = subprocess.run([SUSHIC, "case.sushi", "-o", "case"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    if built.returncode != 0:
        pytest.fail(f"compilation failed:\n{built.stdout}\n{built.stderr}")
    return subprocess.run([str(tmp_path / "case")], capture_output=True, text=True)


@pytest.mark.parametrize("label,setup,call,read,expected", WRITE_ROWS,
                         ids=[row[0] for row in WRITE_ROWS])
def test_write_reaches_the_owner(tmp_path, label, setup, call, read, expected):
    """A mutating built-in must reach the owner, whatever shape the receiver has."""
    source = PRELUDE + f"""
fn main() i32:
    {setup}
    {call}
    println("{{{read}[0]}} {{{read}[1]}} {{{read}[2]}}")
    return Result.Ok(0)
"""
    result = _run(tmp_path, source)
    assert result.stdout.strip() == expected, f"{label}: write did not reach the owner"


@pytest.mark.parametrize("label,setup,read", READ_ROWS, ids=[row[0] for row in READ_ROWS])
def test_read_methods_agree_across_receiver_shapes(tmp_path, label, setup, read):
    """len, get, iter, hash and clone read the same array through every receiver shape."""
    source = PRELUDE + f"""
fn main() i32:
    {setup}
    println({read}.len())
    println({read}.get(1).realise(0))
    let i32 total = 0
    foreach(n in {read}.iter()):
        total := total + n
    println(total)
    let i32[3] copy = {read}.clone()
    println({read}.hash() == copy.hash())
    return Result.Ok(0)
"""
    result = _run(tmp_path, source)
    assert result.stdout.split() == ["3", "2", "6", "true"], f"{label}: {result.stdout!r}"


def test_write_to_a_constant_is_rejected(tmp_path):
    """CE2096 speaks first, so no store into `.rodata` can be emitted (#480, question 2)."""
    (tmp_path / "case.sushi").write_text(PRELUDE + """
fn main() i32:
    TRIPLE.fill(9)
    return Result.Ok(0)
""", encoding="utf-8")
    built = subprocess.run([SUSHIC, "case.sushi", "-o", "case"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode == 2
    assert "CE2096" in built.stdout + built.stderr
