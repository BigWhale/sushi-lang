"""A private perk in a constraint: which of the two codes answers, per declaration kind.

`docs/design/visibility.md` has two rules and no third. CE4011 is the USE-SITE rule:
another unit names a private perk, in an implementation or a constraint. CE3010 is the
LEAK rule: the perk's OWN unit puts it in a PUBLIC signature. A declaration that carries
a marker reads its own; an extension reads its TARGET type's (Ruling 2), and a builtin
target has none to read.

The two rules partition on one question -- is the perk nameable in the declaring unit --
so a cell answers exactly one code, or none. An `EXPECT_ERROR_CODE` directive is a
substring and cannot pin "one fault, one diagnostic", so the code SET is asserted here,
for every kind that carries a constraint (#692).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, needs_sushic

_CODE = re.compile(r"\b(?:CE|CW)\d{4}\b")

PERK = """\
perk Hidden:
    fn hush() i32
"""

TYPES = """\
public struct Box:
    i32 n

struct Crate:
    i32 n

public struct Wrap@(T):
    T v

struct Bag@(T):
    T v

extend Box with Hidden:
    fn hush() i32:
        return self.n
"""

MAIN = """\
fn main() i32:
    return Result.Ok(0)
"""

# One declaration per kind. `{mark}` is the declaration's own marker where it has one,
# `{target}` the extension's target type where it reads the target's instead.
DECLARATIONS = {
    "function": "{mark}fn use_it@(T: Hidden)(T x) i32:\n    return Result.Ok(x.hush())\n",
    "struct": "{mark}struct Pair@(T: Hidden):\n    T a\n",
    "enum": "{mark}enum Opt@(T: Hidden):\n    Some(T)\n    Nope\n",
    "extension": "extend {target} g@(U: Hidden)(U x) i32:\n    return x.hush()\n",
    "static": "extend {target} static mk@(U: Hidden)(U x) i32:\n    return x.hush()\n",
    "generic target": "extend {target} g@(U: Hidden)(U x) i32:\n    return x.hush()\n",
}

# What "public" and "private" spell for each kind.
SPELLINGS = {
    "function": {"public": {"mark": "public "}, "private": {"mark": ""}},
    "struct": {"public": {"mark": "public "}, "private": {"mark": ""}},
    "enum": {"public": {"mark": "public "}, "private": {"mark": ""}},
    "extension": {"public": {"target": "Box"}, "private": {"target": "Crate"},
                  "builtin": {"target": "i32"}},
    "static": {"public": {"target": "Box"}, "private": {"target": "Crate"},
               "builtin": {"target": "i32"}},
    "generic target": {"public": {"target": "Wrap@(T)"},
                       "private": {"target": "Bag@(T)"}},
}


def _cells():
    for kind, spellings in SPELLINGS.items():
        for visibility in spellings:
            yield kind, visibility


def _declaration(kind: str, visibility: str) -> str:
    return DECLARATIONS[kind].format(**SPELLINGS[kind][visibility])


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


def _codes(out: str) -> set[str]:
    return set(_CODE.findall(out))


@needs_sushic
@pytest.mark.parametrize("kind,visibility", list(_cells()))
def test_another_unit_answers_the_use_site_rule_alone(tmp_path, kind, visibility):
    """Row 1 of the ruling's table: another unit than the perk's, any marker: CE4011."""
    out = _compile(tmp_path, {
        "helper.sushi": PERK + "\n" + TYPES,
        "main.sushi": 'use "helper"\n\n' + _declaration(kind, visibility) + "\n" + MAIN,
    })
    assert _codes(out) == {"CE4011"}, out


@needs_sushic
@pytest.mark.parametrize("kind", sorted(SPELLINGS))
def test_the_own_unit_public_declaration_leaks(tmp_path, kind):
    """Row 2: the perk's own unit, a public declaration or target: CE3010."""
    out = _compile(tmp_path, {
        "main.sushi": PERK + "\n" + TYPES + "\n" + _declaration(kind, "public") + "\n" + MAIN,
    })
    assert _codes(out) == {"CE3010"}, out


@needs_sushic
@pytest.mark.parametrize("kind,visibility", [
    (kind, visibility) for kind, visibility in _cells() if visibility != "public"])
def test_the_own_unit_private_declaration_is_allowed(tmp_path, kind, visibility):
    """Row 3: the perk's own unit, a private declaration or target -- and a builtin
    target, which carries no marker to promise with -- compiles clean."""
    out = _compile(tmp_path, {
        "main.sushi": PERK + "\n" + TYPES + "\n" + _declaration(kind, visibility) + "\n" + MAIN,
    })
    assert _codes(out) == set(), out
