"""The gate on #462: three spellings, ONE copy.

`extend`, `extend_range` and `ss` are the same operation with different arguments. If each
grew its own emitter, each would grow its own bounds rule and its own idea of what the
source owns -- the shape the fixed-array receiver bug (#480) took, where nine sites carried
nine address rules and two of them were silently wrong.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

SUSHIC = shutil.which("sushic")
pytestmark = pytest.mark.skipif(SUSHIC is None,
                                reason="sushic not on PATH (run under `uv run pytest`)")

PRELUDE = """use <io/stdio>

"""


def _run(tmp_path, body: str) -> str:
    (tmp_path / "main.sushi").write_text(PRELUDE + body, encoding="utf-8")
    built = subprocess.run(["sushic", "main.sushi", "-o", "out"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode in (0, 1), built.stdout + built.stderr
    return subprocess.run([str(tmp_path / "out")], capture_output=True, text=True).stdout


def _ir(tmp_path, body: str) -> str:
    (tmp_path / "main.sushi").write_text(PRELUDE + body, encoding="utf-8")
    built = subprocess.run(["sushic", "--write-ll", "--opt", "none", "main.sushi", "-o", "out"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode in (0, 1), built.stdout + built.stderr
    return (tmp_path / "out.ll").read_text(encoding="utf-8")


def test_the_three_spellings_agree(tmp_path):
    """`extend`, `extend_range` over the whole source, and `ss` all copy the same bytes."""
    out = _run(tmp_path, """fn main() i32:
    let i32[] src = from([1, 2, 3])

    let i32[] a = new()
    a.extend(src)

    let i32[] b = new()
    b.extend_range(src, 0, src.len())

    let i32[] c = src.ss(0, src.len())

    println("{a.len()}{b.len()}{c.len()} {a[2]}{b[2]}{c[2]}")
    return Result.Ok(0)
""")
    assert out.strip() == "333 333"


def test_a_plain_element_type_copies_with_a_memcpy(tmp_path):
    """A shallow store of a plain value IS the value, so the walk would buy nothing."""
    ir = _ir(tmp_path, """fn main() i32:
    let i32[] src = from([1, 2, 3])
    let i32[] out = new()
    out.extend(src)
    return Result.Ok(out.len())
""")
    assert "llvm.memcpy" in ir, "a plain element type did not reach the memcpy"
    assert "extend_clone" not in ir, "a plain element type walked and cloned"


def test_an_owning_element_type_clones_per_slot(tmp_path):
    """Ruling 7, generalized: a bulk write borrows its source and copies every slot."""
    ir = _ir(tmp_path, """fn main() i32:
    let string[] src = from(["towel"])
    let string[] out = new()
    out.extend(src)
    return Result.Ok(out.len())
""")
    assert "extend_clone" in ir, "an owning element type did not walk and clone"


def test_the_source_survives_every_spelling(tmp_path):
    """A borrow, so the source is usable and independent afterwards."""
    out = _run(tmp_path, """fn main() i32:
    let string[] src = from(["towel", "guide"])
    let string[] a = new()
    a.extend(src)
    let string[] b = src.ss(0, 2)
    println("{src[0]} {a[0]} {b[0]} {src.len()}")
    return Result.Ok(0)
""")
    assert out.strip() == "towel towel towel 2"


def test_a_fixed_array_is_a_source_but_not_a_destination(tmp_path):
    """`.ss()` takes either kind; growing needs a length that is not part of the type."""
    out = _run(tmp_path, """fn main() i32:
    let i32[3] fixed = [7, 8, 9]
    let i32[] out = new()
    out.extend(fixed)
    let i32[] tail = fixed.ss(1, 2)
    println("{out.len()} {tail[0]}")
    return Result.Ok(0)
""")
    assert out.strip() == "3 8"

    (tmp_path / "bad.sushi").write_text(PRELUDE + """fn main() i32:
    let i32[3] fixed = [7, 8, 9]
    let i32[] src = from([1])
    fixed.extend(src)
    return Result.Ok(0)
""", encoding="utf-8")
    built = subprocess.run(["sushic", "bad.sushi", "-o", "bad"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode == 2
    assert "CE2023" in built.stdout + built.stderr
