"""A QUALIFIED constraint carets the written name too, not the type parameter (#729).

#706 put every constraint rule on `ConstraintSite.span`, which marks the perk name and
the qualifier with it. `check_qualified_constraints` was not on that seam: it walked
`ast_walk.declarations()` itself and passed the type parameter's own span, so
`@(T: h.Hidden)` read its CE2001 under `T: h.Hidden`.

The cells are the declaration kinds that carry a type parameter. They are here because
the seam is what makes them one rule: a kind that gains type parameters tomorrow is
added to `signature_constraints()` once, and this caret follows it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, needs_sushic

_HEADER = re.compile(r"^[\w./-]+:(?P<line>\d+):(?P<col>\d+): error \[(?P<code>CE\d{4})\]",
                     re.MULTILINE)

TYPES = """\
public struct Box:
    i32 n

public struct Wrap@(T):
    T v
"""

HELPER = """\
public perk Loud:
    fn shout() i32
"""

# One declaration per kind that carries a type parameter. `{c}` is the constraint.
DECLARATIONS = {
    "function": "fn use_it@(T: {c})(T x) i32:\n    return Result.Ok(0)\n",
    "struct": "struct Pair@(T: {c}):\n    T a\n",
    "enum": "enum Opt@(T: {c}):\n    Some(T)\n    Nope\n",
    "extension": "extend Box g@(U: {c})(U x) i32:\n    return 0\n",
    "static": "extend Box static mk@(U: {c})(U x) i32:\n    return 0\n",
    "generic target": "extend Wrap@(T) g@(U: {c})(U x) i32:\n    return 0\n",
}

MAIN_TAIL = "\nfn main() i32:\n    return Result.Ok(0)\n"


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
@pytest.mark.parametrize("kind", list(DECLARATIONS))
def test_an_unbound_alias_carets_the_written_name(tmp_path, kind):
    """CE2001: the qualifier names no namespace. The caret is on `nope.Loud`."""
    main = ('use "types"\n\n'
            + DECLARATIONS[kind].format(c="nope.Loud") + MAIN_TAIL)
    out = _compile(tmp_path, {"types.sushi": TYPES, "main.sushi": main})
    assert _reported(out, "CE2001", main).startswith("nope.Loud)"), out


@needs_sushic
def test_a_missing_member_carets_the_written_name(tmp_path):
    """CE2001: the alias is bound and the unit behind it declares no such perk."""
    main = ('use "helper" as h\n\n'
            "fn use_it@(T: h.Quiet)(T x) i32:\n    return Result.Ok(0)\n" + MAIN_TAIL)
    out = _compile(tmp_path, {"helper.sushi": HELPER, "main.sushi": main})
    assert _reported(out, "CE2001", main).startswith("h.Quiet)"), out


@needs_sushic
def test_the_second_of_two_constraints_carets_its_own_name(tmp_path):
    """The spans are index-aligned: a parameter's own span answers twice."""
    main = ('use "helper" as h\n\n'
            "fn use_it@(T: h.Loud + h.Quiet)(T x) i32:\n    return Result.Ok(0)\n"
            + MAIN_TAIL)
    out = _compile(tmp_path, {"helper.sushi": HELPER, "main.sushi": main})
    assert _reported(out, "CE2001", main).startswith("h.Quiet)"), out
