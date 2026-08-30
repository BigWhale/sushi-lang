"""Whose code a transplanted library template is, when it fails (#471).

A binary `.slib` ships a public generic as a re-parsable source slice, and the consumer
monomorphizes it. The instance is checked at the consumer, so the diagnostics it raises are
the consumer's to render -- and they belong to the LIBRARY. Rendered against the consumer's
file, a span from the slice names a line the consumer never wrote and draws a caret over
whatever text happens to sit there.

A source library already reads correctly, because it arrives as a unit with a provenance.
These assert the binary path reads the same way, and that a consumer's own generic is
untouched.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sushic_path import SUSHIC, needs_sushic


# `a + 1` is legal for an integer T and CE2509 for a string one, and nothing is checked
# until a consumer asks -- so the library builds and the consumer is where it fails.
ERR_LIB = """\
public fn add_one@(T)(nom T a) T:
    return Result.Ok(a + 1)
"""

WARN_LIB = """\
fn helper(i32 n) i32:
    return Result.Ok(n * 3)

public fn through@(T)(nom T a, i32 n) i32:
    return Result.Ok(helper(n)??)
"""

# The consumer's own generic, for the same shape of body.
OWN_GENERIC = """\
fn mine@(T)(nom T a, i32 n) i32:
    return Result.Ok(n)

fn main() i32:
    println("{mine(nom 1, 3).realise(0)}")
    return Result.Ok(0)
"""

STRING_CONSUMER = """\
use <lib/errlib>

fn main() i32:
    let string s = "x"
    let string fallback = "none"
    let string got = add_one(nom s).realise(fallback)
    println(got)
    return Result.Ok(0)
"""

WARN_CONSUMER = """\
use <lib/warnlib>

fn main() i32:
    println("{through(nom 1, 3).realise(0)}")
    return Result.Ok(0)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path, source: str, name: str, kind: str = "binary"):
    libs = tmp_path / "libs"
    libs.mkdir(exist_ok=True)
    _write(tmp_path / f"{name}.sushi", source)
    r = subprocess.run([SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "0.1.0",
                        str(tmp_path / f"{name}.sushi"), "-o", str(libs / f"{name}.slib")],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return {**os.environ, "SUSHI_LIB_PATH": str(libs)}


def _compile(tmp_path: Path, program: str, env: dict | None = None, name: str = "prog"):
    project = tmp_path / name
    _write(project / "main.sushi", program)
    r = subprocess.run([SUSHIC, "main.sushi", "-o", "out"], cwd=project,
                       capture_output=True, text=True, env=env or {**os.environ})
    return r, r.stdout + r.stderr


@needs_sushic
def test_an_error_in_a_binary_template_body_names_the_template(tmp_path):
    env = _build_lib(tmp_path, ERR_LIB, "errlib")
    r, out = _compile(tmp_path, STRING_CONSUMER, env)

    assert r.returncode == 2, out
    assert "CE2509" in out
    # The template, not the consumer's file, and the library says which template.
    assert "template:errlib:add_one" in out
    assert "./main.sushi:" not in out


@needs_sushic
def test_the_caret_is_drawn_over_the_template_source(tmp_path):
    env = _build_lib(tmp_path, ERR_LIB, "errlib")
    _r, out = _compile(tmp_path, STRING_CONSUMER, env)

    # The library shipped the slice, so there IS a line to show -- and the caret marks
    # the `+` in it, not a column of the consumer's own text.
    assert "return Result.Ok(a + 1)" in out


@needs_sushic
def test_the_diagnostic_says_which_library_and_why_it_is_here(tmp_path):
    env = _build_lib(tmp_path, ERR_LIB, "errlib")
    _r, out = _compile(tmp_path, STRING_CONSUMER, env)

    assert "'errlib' 0.1.0" in out
    assert "use <lib/errlib>" in out


@needs_sushic
def test_a_warning_from_a_binary_template_body_is_attributed_too(tmp_path):
    env = _build_lib(tmp_path, WARN_LIB, "warnlib")
    r, out = _compile(tmp_path, WARN_CONSUMER, env)

    # A warning-only build exits 1.
    assert r.returncode == 1, out
    assert "CW1001" in out
    assert "template:warnlib:through" in out
    # The old shape drew the consumer's line 1 as the source line, with a caret at the
    # template's column. The note may still name the `use`; the SOURCE LINE may not.
    assert "| use <lib/warnlib>" not in out


@needs_sushic
def test_a_consumer_generic_still_reports_against_the_consumer(tmp_path):
    # The caret work is what makes these render at all: a real file, a real line, and a
    # marker the width of the thing it marks. A library body must not change that.
    r, out = _compile(tmp_path, OWN_GENERIC, name="own")

    assert r.returncode == 1, out
    assert "CW1001" in out
    assert "main.sushi:1:" in out
    assert "fn mine@(T)(nom T a, i32 n) i32:" in out
    assert "template:" not in out


@needs_sushic
def test_a_source_library_still_reports_against_its_own_file(tmp_path):
    env = _build_lib(tmp_path, ERR_LIB, "errlib", kind="source")
    r, out = _compile(tmp_path, STRING_CONSUMER, env, name="srcprog")

    assert r.returncode == 2, out
    assert "errlib.sushi:2:22" in out
    assert "return Result.Ok(a + 1)" in out
    assert "is a source library" in out
