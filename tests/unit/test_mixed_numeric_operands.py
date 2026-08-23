"""One rule decides CE2510, and it covers the bitwise operators too.

`& | ^` used to accept two widths where `+` refused them, and the backend then
widened or truncated the right operand on its own -- so `low | wide` (u8, u32)
compiled clean and printed a value with the high bits cut off (#438). The rule now
lives in one function that arithmetic, comparison and the bitwise operators all
call. These cases hold the collapse in place: the bitwise half must stay, the
arithmetic half must not have moved, and a shift count must stay free.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _compile(tmp_path: Path, source: str) -> str:
    (tmp_path / "widths.sushi").write_text(source, encoding="utf-8")
    result = subprocess.run(
        ["sushic", "widths.sushi"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    return result.stderr


def _count(stderr: str, code: str) -> int:
    return len(re.findall(rf"(?:error|warning) \[{code}\]:", stderr))


def test_ce2510_is_emitted_from_one_place():
    """The rule is one function. A second emitter is a second rule that will drift."""
    sites = subprocess.run(
        ["grep", "-rn", "ERR.CE2510", "sushi_lang"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    assert len(sites) == 1, "\n".join(sites)
    assert "passes/types/expressions.py" in sites[0], sites[0]


def test_bitwise_or_of_two_widths_is_ce2510(tmp_path):
    """The truncating case: 0x1FF reached a u8 result as 0xFF, and said nothing."""
    stderr = _compile(tmp_path, """fn main() i32:
    let u8 low = 0x05
    let u32 wide = 0x1FF
    let u8 merged = low | wide
    println("{merged}")
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2510") == 1, stderr


def test_bitwise_and_and_xor_of_two_widths_are_ce2510(tmp_path):
    """All three of & | ^ carry the rule, not just the one that was reported."""
    stderr = _compile(tmp_path, """fn main() i32:
    let u16 narrow = 0xFF00
    let u64 wide = 0x00FF
    let u16 kept = narrow & wide
    let u16 flipped = narrow ^ wide
    println("{kept} {flipped}")
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2510") == 2, stderr


def test_arithmetic_of_two_widths_is_still_ce2510(tmp_path):
    """The arithmetic path lost its own copy of the check. It must behave the same."""
    stderr = _compile(tmp_path, """fn main() i32:
    let u8 small = 5
    let u32 big = 9
    let u32 sum = small + big
    println("{sum}")
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2510") == 1, stderr


def test_comparison_of_two_widths_is_still_ce2510(tmp_path):
    """The comparison arm of the same collapsed check."""
    stderr = _compile(tmp_path, """fn main() i32:
    let i16 left = 5
    let i64 right = 9
    if (left == right):
        println("equal")
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2510") == 1, stderr


def test_a_shift_count_of_another_width_is_accepted(tmp_path):
    """A count says how far to move, not what the result is. Its type is free."""
    stderr = _compile(tmp_path, """fn main() i32:
    let u64 value = 8
    let u8 places = 8
    let u64 shifted = value << places
    println("{shifted}")
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2510") == 0, stderr


def test_a_bare_literal_operand_is_not_a_mixed_pair(tmp_path):
    """A literal takes its sibling's type, so `mask & 0xF0` is one width."""
    stderr = _compile(tmp_path, """fn main() i32:
    let u8 mask = 0x0F
    let u8 kept = mask & 0xF0
    println("{kept}")
    return Result.Ok(0)
""")
    assert _count(stderr, "CE2510") == 0, stderr
