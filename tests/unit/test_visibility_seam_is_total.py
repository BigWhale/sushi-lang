"""Every declaration kind has a visibility answer, and the answer is written down.

`public` reached one declaration out of six, so five kinds had no answer and nobody had to
say so. `docs/design/visibility.md` rules on all of them, and the four sets in
`semantics/visibility.py` are that ruling in code: a kind carries its own marker, follows
the declaration it is part of, follows the type it is attached to, or has no visibility.

A kind in none of them is a kind whose rule nobody decided, which is how a hole gets in.
"""
from __future__ import annotations


from types import SimpleNamespace

from sushi_lang.semantics.visibility import (
    CARRIES_MARKER,
    EXTENSION_METHOD,
    FOLLOWS_DECLARATION,
    FOLLOWS_TARGET_TYPE,
    NO_VISIBILITY,
    VisibilityTable,
    extension_method_name,
    record_declaration,
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


def test_an_unrecorded_name_is_visible_from_anywhere():
    """What the compiler synthesizes has no declaration, and must stay nameable."""
    table = VisibilityTable()
    assert table.is_visible_from("struct", "List<i32>", "some_unit")
    assert table.origin("struct", "List<i32>") is None


def test_an_extension_method_carries_its_own_marker():
    """`docs/design/extension-visibility.md` R4 to R7: the marker answers, not the target.

    A conversion is the walk kind "extension" and keeps no marker (R8), so the method
    kind is a kind of its own.
    """
    assert EXTENSION_METHOD in CARRIES_MARKER
    assert EXTENSION_METHOD != "extension"


def _extension(name: str, is_public: bool) -> SimpleNamespace:
    return SimpleNamespace(name=name, name_span=None, loc=None, is_public=is_public)


def test_an_extension_method_is_filed_under_its_target():
    """One method name on two targets is two declarations, and neither contests the other."""
    table = VisibilityTable()
    for unit, target, is_public in (("a", "i32", False), ("b", "string", True)):
        record_declaration(table, EXTENSION_METHOD, _extension("twice", is_public),
                           unit_name=unit, filename=f"{unit}.sushi",
                           name=extension_method_name(target, "twice"))

    on_i32 = table.origin(EXTENSION_METHOD, "i32.twice")
    on_string = table.origin(EXTENSION_METHOD, "string.twice")
    assert on_i32 is not None and on_i32.unit_name == "a" and not on_i32.is_public
    assert on_string is not None and on_string.unit_name == "b" and on_string.is_public
    assert not table.contested
    assert table.origin(EXTENSION_METHOD, "twice") is None


def test_one_method_on_one_target_in_two_units_is_two_origins():
    """C5: each unit keeps its own private extension, and the table keeps both."""
    table = VisibilityTable()
    for unit in ("a", "b"):
        record_declaration(table, EXTENSION_METHOD, _extension("twice", False),
                           unit_name=unit, filename=f"{unit}.sushi",
                           name=extension_method_name("i32", "twice"))
    units = [origin.unit_name
             for origin in table.origins(EXTENSION_METHOD, "i32.twice")]
    assert units == ["a", "b"]
