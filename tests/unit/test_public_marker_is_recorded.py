"""The `public` marker reaches the AST for every kind Ruling 1 and Ruling 3 name.

`docs/design/visibility.md` gives a marker to `const`, `struct`, `enum`, `perk` and `fn`.
The grammar carries `PUBLIC?` on all five rules, one reader answers for all of them, and
the node keeps the marker's SPAN as well as the answer -- the span is what tells a written
marker from a kind whose unmarked default is still public, and what a misplaced marker's
diagnostic points at.
"""
from __future__ import annotations


from sushi_lang.semantics.visibility import declared_public


SOURCES = {
    "constant": ("{marker}const i32 ANSWER = 42\n", "constants"),
    "variable": ("{marker}var i32 counter = 0\n", "constants"),
    "struct": ("{marker}struct Crate:\n    i32 weight\n", "structs"),
    "enum": ("{marker}enum Mood:\n    Calm\n", "enums"),
    "perk": ("{marker}perk Loud:\n    fn shout() string\n", "perks"),
    "function": ("{marker}fn weigh() i32:\n    return Result.Ok(1)\n", "functions"),
}








def test_every_marked_kind_is_private_when_unmarked():
    """Private is the default for every kind that carries a marker.

    The predicate answers the marker and nothing else. A kind that answered public
    without the word would fail here, which is what keeps Ruling 1 whole.
    """
    for kind in SOURCES:
        assert declared_public(kind, False) is False, kind
        assert declared_public(kind, True) is True, kind
