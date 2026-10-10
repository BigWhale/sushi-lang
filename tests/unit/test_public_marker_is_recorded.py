"""The `public` marker reaches the AST for every kind Ruling 1 and Ruling 3 name.

`docs/design/visibility.md` gives a marker to `const`, `struct`, `enum`, `perk` and `fn`,
and `docs/design/extension-visibility.md` gives one to an extension method and a static
method. The grammar carries `PUBLIC?` on each of these rules, one reader answers for all
of them, and the node keeps the marker's SPAN as well as the answer -- the span is what
tells a written marker from a kind whose unmarked default is still public, and what a
misplaced marker's diagnostic points at.
"""
from __future__ import annotations


from sushi_lang.semantics.visibility import CARRIES_MARKER, declared_public


SOURCES = {
    "constant": ("{marker}const i32 ANSWER = 42\n", "constants"),
    "variable": ("{marker}var i32 counter = 0\n", "constants"),
    "struct": ("{marker}struct Crate:\n    i32 weight\n", "structs"),
    "enum": ("{marker}enum Mood:\n    Calm\n", "enums"),
    "perk": ("{marker}perk Loud:\n    fn shout() string\n", "perks"),
    "function": ("{marker}fn weigh() i32:\n    return Result.Ok(1)\n", "functions"),
    "extension method": ("{marker}extend i32 twice() i32:\n    return self * 2\n",
                         "extensions"),
}


def test_every_marked_kind_is_private_when_unmarked():
    """Private is the default for every kind that carries a marker.

    The predicate answers the marker and nothing else. A kind that answered public
    without the word would fail here, which is what keeps Ruling 1 whole.
    """
    for kind in SOURCES:
        assert declared_public(kind, False) is False, kind
        assert declared_public(kind, True) is True, kind


def test_the_matrix_names_every_kind_that_carries_a_marker():
    """A new kind in `CARRIES_MARKER` needs its row here, and a row needs its kind."""
    assert set(SOURCES) == CARRIES_MARKER
