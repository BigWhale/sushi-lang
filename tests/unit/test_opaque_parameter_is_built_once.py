"""An opaque type parameter is built in ONE place: the collect pass of a template (#1070).

The owner of a type parameter is its identity, so a second builder could make a `T` that
compares unequal to the `T` of the same template, and the template check would then read
two types where the program wrote one. `opaque_type_params` in
`passes/collect/functions.py` is the one builder; the check copy, the predicates and the
notes read what it built.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

# The one module that may build a TypeParameter with an owner.
ALLOWED = {"semantics/passes/collect/functions.py"}

_BUILD = re.compile(r"TypeParameter\([^)]*\bowner\s*=")


def test_only_the_collect_pass_builds_an_opaque_parameter():
    builders = sorted(
        str(path.relative_to(ROOT)) for path in ROOT.rglob("*.py")
        if _BUILD.search(path.read_text(encoding="utf-8")))
    assert set(builders) <= ALLOWED, builders
    assert builders, "the scan found no builder at all: the pattern is out of date"
