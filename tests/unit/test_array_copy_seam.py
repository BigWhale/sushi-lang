"""The gate on #462: four spellings, ONE copy.

`extend`, `extend_range`, `s` and `ss` are the same operation with different arguments. If
each grew its own emitter, each would grow its own bounds rule and its own idea of what the
source owns -- the shape the fixed-array receiver bug (#480) took, where nine sites carried
nine address rules and two of them were silently wrong.
"""
from __future__ import annotations

import subprocess

from sushic_path import SUSHIC, needs_sushic

pytestmark = needs_sushic

PRELUDE = """use <io/stdio>

"""


def _run(tmp_path, body: str) -> str:
    (tmp_path / "main.sushi").write_text(PRELUDE + body, encoding="utf-8")
    built = subprocess.run([SUSHIC, "main.sushi", "-o", "out"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode in (0, 1), built.stdout + built.stderr
    return subprocess.run([str(tmp_path / "out")], capture_output=True, text=True).stdout


def _ir(tmp_path, body: str) -> str:
    (tmp_path / "main.sushi").write_text(PRELUDE + body, encoding="utf-8")
    built = subprocess.run([SUSHIC, "--write-ll", "--opt", "none", "main.sushi", "-o", "out"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode in (0, 1), built.stdout + built.stderr
    return (tmp_path / "out.ll").read_text(encoding="utf-8")


def test_the_four_spellings_agree(tmp_path):
    """All four copy the same bytes when they name the same range."""
    out = _run(tmp_path, """fn main() i32:
    let i32[] src = from([1, 2, 3])

    let i32[] a = new()
    a.extend(src)

    let i32[] b = new()
    b.extend_range(src, 0, src.len())

    let i32[] c = src.ss(0, src.len())
    let i32[] d = src.s(0, src.len())

    println("{a.len()}{b.len()}{c.len()}{d.len()} {a[2]}{b[2]}{c[2]}{d[2]}")
    return Result.Ok(0)
""")
    assert out.strip() == "3333 3333"


def test_s_is_ss_with_the_end_subtracted(tmp_path):
    """`s(a, b)` is `ss(a, b - a)`. The two differ where they read their arguments and
    nowhere else, which is what stops one of them growing a bounds rule of its own."""
    out = _run(tmp_path, """fn main() i32:
    let i32[] src = from([10, 20, 30, 40, 50, 60])
    let i32[] by_end = src.s(2, 5)
    let i32[] by_len = src.ss(2, 3)
    println("{by_end.len()}{by_len.len()} {by_end[0]}{by_len[0]} {by_end[2]}{by_len[2]}")
    return Result.Ok(0)
""")
    assert out.strip() == "33 3030 5050"


def test_the_slices_clamp_exactly_as_their_string_twins_do(tmp_path):
    """`string.s` and `string.ss` have always clamped, and these are their array twins.

    Every row here is the string answer for the same arguments: "hello".s(-2, 3) is "hel",
    .s(9, 12) and .s(3, 1) are "", .ss(2, 99) is "llo", .ss(2, -2) is "".
    """
    out = _run(tmp_path, """fn main() i32:
    let i32[] src = from([1, 2, 3, 4, 5])
    let i32 neg = 0 - 2
    let i32[] a = src.s(neg, 3)
    let i32[] b = src.s(9, 12)
    let i32[] c = src.s(3, 1)
    let i32[] d = src.ss(2, 99)
    let i32[] e = src.ss(2, neg)
    println("{a.len()}{b.len()}{c.len()}{d.len()}{e.len()}")
    return Result.Ok(0)
""")
    assert out.strip() == "30030"


def test_no_array_copy_emits_a_runtime_trap(tmp_path):
    """The copy family answers a bad range with data, never with an exit."""
    ll = _ir(tmp_path, """fn main() i32:
    let i32[] src = from([1, 2, 3])
    let i32[] out = new()
    out.extend_range(src, 1, 99)
    let i32[] part = src.s(0, 99)
    return Result.Ok(out.len() + part.len())
""")
    assert "RE2020" not in ll, "a copy still traps out of range"
    assert "RE2024" not in ll, "a copy still traps a negative count"


def test_the_source_survives_every_spelling(tmp_path):
    """A borrow, so the source is usable and independent afterwards."""
    out = _run(tmp_path, """fn main() i32:
    let string[] src = from(["towel", "guide"])
    let string[] a = new()
    a.extend(src)
    let string[] b = src.ss(0, 2)
    let string[] c = src.s(0, 2)
    println("{src[0]} {a[0]} {b[0]} {c[0]} {src.len()}")
    return Result.Ok(0)
""")
    assert out.strip() == "towel towel towel towel 2"


def test_a_fixed_array_is_a_source_but_not_a_destination(tmp_path):
    """`.ss()` takes either kind; growing needs a length that is not part of the type."""
    out = _run(tmp_path, """fn main() i32:
    let i32[3] fixed = [7, 8, 9]
    let i32[] out = new()
    out.extend(fixed)
    let i32[] tail = fixed.ss(1, 2)
    let i32[] by_end = fixed.s(1, 3)
    println("{out.len()} {tail[0]} {by_end[1]}")
    return Result.Ok(0)
""")
    assert out.strip() == "3 8 9"

    (tmp_path / "bad.sushi").write_text(PRELUDE + """fn main() i32:
    let i32[3] fixed = [7, 8, 9]
    let i32[] src = from([1])
    fixed.extend(src)
    return Result.Ok(0)
""", encoding="utf-8")
    built = subprocess.run([SUSHIC, "bad.sushi", "-o", "bad"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode == 2
    assert "CE2023" in built.stdout + built.stderr
