"""Every declaration kind has a visibility answer, and the answer is written down.

`public` reached one declaration out of six, so five kinds had no answer and nobody had to
say so. `docs/design/visibility.md` rules on all of them, and the four sets in
`semantics/visibility.py` are that ruling in code: a kind carries its own marker, follows
the declaration it is part of, follows the type it is attached to, or has no visibility.

A kind in none of them is a kind whose rule nobody decided, which is how a hole gets in.
"""
from __future__ import annotations


from types import SimpleNamespace

from sushi_lang.semantics.namespaces import UnitScope
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType
from sushi_lang.semantics.visibility import (
    CARRIES_MARKER,
    CONVERSION,
    EXTENSION_METHOD,
    FOLLOWS_DECLARATION,
    FOLLOWS_TARGET_TYPE,
    NO_VISIBILITY,
    DeclOrigin,
    MethodReach,
    VisibilityTable,
    extension_method_name,
    extension_reach,
    extensions_collide,
    hides_import,
    home_unit_of,
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


def test_a_conversion_follows_its_target_and_a_method_carries_a_marker():
    """R8: the two `extend` kinds have two rules, and neither is a special case."""
    assert CONVERSION in FOLLOWS_TARGET_TYPE
    assert EXTENSION_METHOD in CARRIES_MARKER
    assert "extension" not in FOLLOWS_TARGET_TYPE | CARRIES_MARKER


# The program of the method matrix: `shapes` declares the type `Gem`, `bytes` declares
# extensions on `i32`, `main` imports `bytes` flat, `alias` imports it behind `as`, and
# `other` imports nothing.
_GEM = SimpleNamespace(name="Gem")
_I32 = BuiltinType.I32
_SCOPES = {
    "main": UnitScope(unit="main", units=("bytes",), everything=False),
    "alias": UnitScope(unit="alias", aliased_units=("bytes",), everything=False),
    "other": UnitScope(unit="other", everything=False),
}


def _table() -> VisibilityTable:
    table = VisibilityTable()
    table.record(DeclOrigin(kind="struct", name="Gem", unit_name="shapes"))
    return table


def _method(unit, is_public, *, target=_I32, is_static=False) -> SimpleNamespace:
    return SimpleNamespace(unit_name=unit, is_public=is_public, target_type=target,
                           is_static=is_static, name_span=None, loc=None, filename=None)


def _reach(method, target, asker) -> MethodReach:
    return extension_reach(_table(), method, target, asker, _SCOPES.get(asker))


def test_the_method_matrix():
    """R4 to R6 and R1, one row per rule, for each kind of calling unit."""
    rows = [
        # (declaring unit, public, target, asker, expected)
        ("bytes", False, _I32, "bytes", MethodReach.VISIBLE),     # own unit
        ("bytes", False, _I32, "main", MethodReach.PRIVATE),      # R4
        ("shapes", True, _GEM, "other", MethodReach.VISIBLE),     # R5, no import
        ("bytes", True, _GEM, "other", MethodReach.NOT_IMPORTED),  # R6 on another type
        ("bytes", True, _I32, "main", MethodReach.VISIBLE),       # R6, flat import
        ("bytes", True, _I32, "alias", MethodReach.VISIBLE),      # R6, aliased import
        ("bytes", True, _I32, "other", MethodReach.NOT_IMPORTED),  # R6, no import
        ("bytes", True, DynamicArrayType(_I32), "other", MethodReach.NOT_IMPORTED),
        ("collections/strings", True, _I32, "other", MethodReach.VISIBLE),  # R1
        ("collections/strings", False, _I32, "other", MethodReach.PRIVATE),
        (None, False, _I32, "other", MethodReach.VISIBLE),        # synthesized
        ("bytes", False, _I32, None, MethodReach.VISIBLE),        # a scratch reader
    ]
    for unit, is_public, target, asker, expected in rows:
        got = _reach(_method(unit, is_public, target=target), target, asker)
        assert got is expected, (unit, is_public, target, asker, got)


def test_an_array_and_a_builtin_type_have_no_home_unit():
    table = _table()
    assert home_unit_of(table, _GEM) == "shapes"
    assert home_unit_of(table, _I32) is None
    assert home_unit_of(table, DynamicArrayType(_GEM)) is None


def test_which_two_declarations_collide():
    """C1 and C2 for a home unit collide; C5 and two imported ones (C3, C4) do not."""
    table = _table()
    assert extensions_collide(table, _I32, _method("a", False), _method("a", True))
    assert extensions_collide(table, _GEM, _method("shapes", True, target=_GEM),
                              _method("main", False, target=_GEM))
    assert not extensions_collide(table, _GEM, _method("shapes", False, target=_GEM),
                                  _method("main", False, target=_GEM))
    assert not extensions_collide(table, _I32, _method("a", True), _method("b", True))


def test_an_own_extension_hides_only_an_imported_one():
    """C3 is a warning for an import alone: R1 and R5 are errors at the declaration."""
    table = _table()
    own = _method("main", False)
    assert hides_import(table, own, _method("bytes", True), _SCOPES["main"])
    assert not hides_import(table, own, _method("bytes", False), _SCOPES["main"])
    assert not hides_import(table, _method("other", False), _method("bytes", True),
                            _SCOPES["other"])
    assert not hides_import(table, own, _method("bytes", True, is_static=True),
                            _SCOPES["main"])
