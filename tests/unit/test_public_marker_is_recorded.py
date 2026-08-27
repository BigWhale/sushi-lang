"""The `public` marker reaches the AST for every kind Ruling 1 and Ruling 3 name.

`docs/design/visibility.md` gives a marker to `const`, `struct`, `enum`, `perk` and `fn`.
The grammar carries `PUBLIC?` on all five rules, one reader answers for all of them, and
the node keeps the marker's SPAN as well as the answer -- the span is what tells a written
marker from a kind whose unmarked default is still public, and what a misplaced marker's
diagnostic points at.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.visibility import UNMARKED_IS_PUBLIC, declared_public


SOURCES = {
    "constant": ("{marker}const i32 ANSWER = 42\n", "constants"),
    "struct": ("{marker}struct Crate:\n    i32 weight\n", "structs"),
    "enum": ("{marker}enum Mood:\n    Calm\n", "enums"),
    "perk": ("{marker}perk Loud:\n    fn shout() string\n", "perks"),
    "function": ("{marker}fn weigh() i32:\n    return Result.Ok(1)\n", "functions"),
}


def _declaration(kind: str, marker: str):
    source, field = SOURCES[kind]
    program, _tree = parse_to_ast(source.format(marker=marker))
    declarations = getattr(program, field)
    assert len(declarations) == 1, declarations
    return declarations[0]


@pytest.mark.parametrize("kind", sorted(SOURCES))
def test_the_marker_is_recorded_with_its_span(kind):
    marked = _declaration(kind, "public ")
    assert marked.public_span is not None
    assert marked.is_public is True


@pytest.mark.parametrize("kind", sorted(SOURCES))
def test_an_unmarked_declaration_carries_no_span(kind):
    plain = _declaration(kind, "")
    assert plain.public_span is None
    # What the absence MEANS is a per-kind ruling that Phase 2 flips one kind at a time.
    assert plain.is_public is declared_public(kind, False)


def test_every_marked_kind_is_private_when_unmarked():
    """Phase 2 emptied the set: private is the default for all five kinds."""
    assert not UNMARKED_IS_PUBLIC
    for kind in SOURCES:
        assert declared_public(kind, False) is False, kind
        assert declared_public(kind, True) is True, kind
