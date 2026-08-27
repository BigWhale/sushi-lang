"""Every declaration kind has a visibility answer, and the answer is written down.

`public` reached one declaration out of six, so five kinds had no answer and nobody had to
say so. `docs/design/visibility.md` rules on all of them, and the four sets in
`semantics/visibility.py` are that ruling in code: a kind carries its own marker, follows
the declaration it is part of, follows the type it is attached to, or has no visibility.

A kind in none of them is a kind whose rule nobody decided, which is how a hole gets in.
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.ast_walk import declarations
from sushi_lang.semantics.visibility import (
    CARRIES_MARKER,
    FOLLOWS_DECLARATION,
    FOLLOWS_TARGET_TYPE,
    NO_VISIBILITY,
    VisibilityTable,
    record_declarations,
)

# One unit that declares every kind the walk yields.
EVERY_KIND = '''\
const i32 ANSWER = 42

struct Point:
    i32 x

enum Shape:
    Dot
    Poly(i32)

perk Loud:
    fn shout() i32

extend Point with Loud:
    fn shout() i32:
        return 0

unsafe external "C" as libc because "the platform's own abs":
    fn abs(i32 n) i32 = "abs"

fn helper() i32:
    return Result.Ok(1)

extend Point doubled() i32:
    return self.x * 2

fn main() i32:
    return Result.Ok(0)
'''

_CLASSIFIED = CARRIES_MARKER | FOLLOWS_DECLARATION | FOLLOWS_TARGET_TYPE | NO_VISIBILITY


def _program(source: str):
    program, _tree = parse_to_ast(source)
    return program


def _walked_kinds() -> set[str]:
    return {kind for kind, _node in declarations(_program(EVERY_KIND))}


def test_every_walked_kind_is_classified():
    unclassified = sorted(_walked_kinds() - _CLASSIFIED)
    assert not unclassified, (
        f"no visibility rule is written down for: {unclassified}.\n"
        "Add the kind to one of the four sets in semantics/visibility.py. A kind in none "
        "of them silently gets whatever the default happens to be."
    )


def test_no_set_names_a_kind_the_walk_never_yields():
    """The mirror: a classified kind the walk cannot produce is a typo or dead weight."""
    stray = sorted(_CLASSIFIED - _walked_kinds())
    assert not stray, (
        f"semantics/visibility.py classifies kind(s) the walk never yields: {stray}"
    )


def test_the_four_sets_do_not_overlap():
    sets = {
        "CARRIES_MARKER": CARRIES_MARKER,
        "FOLLOWS_DECLARATION": FOLLOWS_DECLARATION,
        "FOLLOWS_TARGET_TYPE": FOLLOWS_TARGET_TYPE,
        "NO_VISIBILITY": NO_VISIBILITY,
    }
    names = list(sets)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            shared = sorted(sets[left] & sets[right])
            assert not shared, f"{left} and {right} both claim {shared}"


def test_a_marker_carrying_kind_is_recorded_with_its_origin():
    table = VisibilityTable()
    record_declarations(table, _program(EVERY_KIND),
                        unit_name="seam", filename="seam.sushi")

    for kind in CARRIES_MARKER:
        recorded = {name for (k, name) in table.by_key if k == kind}
        assert recorded, f"nothing of kind '{kind}' was recorded from a unit that declares one"

    origin = table.origin("struct", "Point")
    assert origin is not None
    assert origin.unit_name == "seam"
    assert origin.filename == "seam.sushi"
    assert origin.name_span is not None, "the note has nothing to point at"


def test_a_kind_that_carries_no_marker_is_not_recorded():
    """Recording one would invite a fence to read it and enforce a rule nobody ruled."""
    table = VisibilityTable()
    record_declarations(table, _program(EVERY_KIND),
                        unit_name="seam", filename="seam.sushi")

    recorded_kinds = {kind for (kind, _name) in table.by_key}
    unexpected = sorted(recorded_kinds - CARRIES_MARKER)
    assert not unexpected, f"recorded kind(s) that carry no marker: {unexpected}"


def test_an_unrecorded_name_is_visible_from_anywhere():
    """What the compiler synthesizes has no declaration, and must stay nameable."""
    table = VisibilityTable()
    assert table.is_visible_from("struct", "List<i32>", "some_unit")
    assert table.origin("struct", "List<i32>") is None
