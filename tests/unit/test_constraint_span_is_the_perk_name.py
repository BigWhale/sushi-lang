"""A constraint diagnostic points at the perk NAME, not at the type parameter (#706).

CLAUDE.md's diagnostic ladder puts the caret under the fault. In `@(T: Hidden)` the
fault is `Hidden`, and `T` is innocent: it is the one name in the constraint the user
may not change. `ConstraintSite.span` is what every constraint rule reads -- CE4011,
CE3010, CE4003 and the out-of-scope CE2001 all take it -- so the span is asserted at
the seam, once, and the two visibility codes are read end to end on top of it.

A parameter with two constraints is the sharpest case: the spans are index-aligned
with the names, so a span taken from the parameter answers the same location twice.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.ast_walk import signature_constraints
from sushic_path import SUSHIC, needs_sushic

_HEADER = re.compile(r"^(?P<file>[\w./-]+):(?P<line>\d+):(?P<col>\d+): error \[(?P<code>CE\d{4})\]",
                     re.MULTILINE)

PERKS = """\
perk Hidden:
    fn hush() i32

perk Loud:
    fn shout() i32
"""

TYPES = """\
public struct Box:
    i32 n

public struct Wrap@(T):
    T v
"""

# One declaration per kind that carries a type parameter. `{c}` is the constraint list.
DECLARATIONS = {
    "function": "fn use_it@(T: {c})(T x) i32:\n    return Result.Ok(0)\n",
    "struct": "struct Pair@(T: {c}):\n    T a\n",
    "enum": "enum Opt@(T: {c}):\n    Some(T)\n    Nope\n",
    "extension": "extend Box g@(U: {c})(U x) i32:\n    return 0\n",
    "static": "extend Box static mk@(U: {c})(U x) i32:\n    return 0\n",
    "generic target": "extend Wrap@(T) g@(U: {c})(U x) i32:\n    return 0\n",
}

CONSTRAINTS = ["Hidden", "Hidden + Loud"]


def _cells():
    for kind in DECLARATIONS:
        for constraint in CONSTRAINTS:
            yield kind, constraint


def _covered(source: str, span) -> str:
    """The text one span marks, on the one line it starts on."""
    line = source.splitlines()[span.line - 1]
    return line[span.col - 1:span.end_col - 1]


@pytest.mark.parametrize("kind,constraint", list(_cells()))
def test_the_seam_spans_the_written_perk_name(kind, constraint):
    """Every `ConstraintSite` marks the perk name and nothing around it."""
    source = PERKS + "\n" + TYPES + "\n" + DECLARATIONS[kind].format(c=constraint)
    program, _tree = parse_to_ast(source)

    sites = list(signature_constraints(program))
    assert [site.perk_name for site in sites] == constraint.split(" + "), sites
    for site in sites:
        assert site.span is not None, site
        assert _covered(source, site.span) == site.perk_name, site


def test_the_seam_spans_a_qualified_constraint_whole():
    """A qualifier is part of what the user wrote, so the caret covers `h.Hidden`."""
    source = ('use "helper" as h\n\n'
              "fn use_it@(T: h.Hidden)(T x) i32:\n    return Result.Ok(0)\n")
    program, _tree = parse_to_ast(source)

    site, = list(signature_constraints(program))
    assert site.namespace == "h"
    assert _covered(source, site.span) == "h.Hidden"


def _compile(tmp_path: Path, units: dict[str, str]) -> str:
    project = tmp_path / "prog"
    project.mkdir()
    for name, source in units.items():
        (project / name).write_text(source, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True,
        env={**os.environ, "NO_COLOR": "1"},
    )
    return result.stdout + result.stderr


def _reported(out: str, code: str, source: str) -> str:
    """The text the diagnostic with this code carets, read back out of the source."""
    for match in _HEADER.finditer(out):
        if match.group("code") != code:
            continue
        line = source.splitlines()[int(match.group("line")) - 1]
        return line[int(match.group("col")) - 1:]
    raise AssertionError(f"no {code} in:\n{out}")


@needs_sushic
def test_the_use_site_rule_carets_the_perk_name(tmp_path):
    """CE4011: another unit constrains with a private perk."""
    main = ('use "helper"\n\n'
            "fn use_it@(T: Hidden)(T x) i32:\n    return Result.Ok(x.hush())\n\n"
            "fn main() i32:\n    return Result.Ok(0)\n")
    out = _compile(tmp_path, {"helper.sushi": PERKS, "main.sushi": main})
    assert _reported(out, "CE4011", main).startswith("Hidden)"), out


@needs_sushic
def test_the_leak_rule_carets_the_perk_name(tmp_path):
    """CE3010: the perk's own unit puts it in a public signature."""
    main = (PERKS + "\n"
            "public fn use_it@(T: Hidden)(T x) i32:\n    return Result.Ok(x.hush())\n\n"
            "fn main() i32:\n    return Result.Ok(0)\n")
    out = _compile(tmp_path, {"main.sushi": main})
    assert _reported(out, "CE3010", main).startswith("Hidden)"), out
