"""CW3003: a library that extends a type it does not declare.

The warning fires at `--lib` build time only (`docs/design/unit-namespaces.md`
section 8). The predicate reads the target type: a builtin or any other type
the library does not declare is a claim on every consumer. A perk
implementation stays quiet, because the consumer's own implementation is the
sanctioned override, so the claim has an escape.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from sushic_path import SUSHIC


REPO = Path(__file__).parents[2]

FOREIGN_LIB = """\
public fn keep() i32:
    return Result.Ok(1)

extend i32 twice_over() i32:
    return self * 2
"""

OWN_LIB = """\
public struct Crate:
    i32 weight

extend Crate heavier() i32:
    return self.weight + 1
"""

SIBLING_ENTRY = """\
use "shape_unit"

extend Circle grow() i32:
    return self.r + 1
"""

SIBLING_UNIT = """\
public struct Circle:
    i32 r
"""

PERK_LIB = """\
public perk Doubler:
    fn doubled() i32

extend i32 with Doubler:
    fn doubled() i32:
        return self * 2
"""

PROGRAM = """\
extend i32 twice_over() i32:
    return self * 2

fn main() i32:
    return Result.Ok(0)
"""


def _build_lib(tmp: Path, name: str, source: str, *companions: tuple[str, str]):
    src = tmp / f"{name}.sushi"
    src.write_text(source, encoding="utf-8")
    for cname, csource in companions:
        (tmp / f"{cname}.sushi").write_text(csource, encoding="utf-8")
    out = tmp / f"{name}.slib"
    r = subprocess.run(
        [SUSHIC, "--lib", "--lib-version", "0.0.1", str(src), "-o", str(out)],
        capture_output=True, text=True, cwd=tmp)
    return r, out


def test_a_builtin_target_warns_at_lib_build(tmp_path):
    r, out = _build_lib(tmp_path, "extlib", FOREIGN_LIB)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "CW3003" in r.stderr
    assert "i32" in r.stderr
    assert out.exists(), "a warning must not stop the build"


def test_an_own_type_target_is_quiet(tmp_path):
    r, _out = _build_lib(tmp_path, "ownlib", OWN_LIB)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CW3003" not in r.stderr


def test_a_sibling_unit_type_is_quiet(tmp_path):
    r, _out = _build_lib(tmp_path, "shapes", SIBLING_ENTRY,
                         ("shape_unit", SIBLING_UNIT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CW3003" not in r.stderr


def test_a_perk_implementation_is_quiet(tmp_path):
    r, _out = _build_lib(tmp_path, "perklib", PERK_LIB)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CW3003" not in r.stderr


def test_a_program_build_is_quiet(tmp_path):
    src = tmp_path / "prog.sushi"
    src.write_text(PROGRAM, encoding="utf-8")
    out = tmp_path / "prog"
    r = subprocess.run([SUSHIC, str(src), "-o", str(out)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CW3003" not in r.stderr
