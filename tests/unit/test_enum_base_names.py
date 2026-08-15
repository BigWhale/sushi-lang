"""The enum-name set the borrow checker needs is built by PRESENCE, not by truthiness.

The borrow checker tells `Box.Full(a)` (an ownership sink) from `xs.push(a)` (a method
call) by the receiver name, so it is handed the base names of every enum. The set used to
be built with an `or`-chain:

    getattr(self.generic_enums, 'by_name', None) or self.generic_enums or {}

An EMPTY `by_name` is falsy, so the chain fell through to the table OBJECT -- a dataclass
with no `__iter__` -- and iterating it raised a bare `TypeError` (F16 of BORROW.md). The
CLI never showed it only because Phase 0 always pre-registers `Result` and `Maybe`, so
`by_name` is never empty in practice. Nothing about that is a property of the code that
built the set.

Presence is the question the code meant to ask: a table HAS a `by_name`, so use it,
empty or not; a plain dict IS the mapping. Truthiness cannot tell "no entries" from
"no such attribute", and that is the whole bug.
"""
from __future__ import annotations

from sushi_lang.semantics.passes.collect.enums import GenericEnumTable
from sushi_lang.semantics.semantic_analyzer import enum_base_names


class _FakeEnum:
    """Stands in for a collected enum: the set only reads the KEYS."""


def test_empty_generic_table_yields_an_empty_set():
    """The F16 repro: an empty `by_name` used to raise TypeError, not return {}."""
    assert enum_base_names(GenericEnumTable(), GenericEnumTable()) == set()


def test_names_are_stripped_to_their_base():
    """A monomorphized generic is interned as `Result<i32, StdError>`; the constructor
    receiver is written bare, so the checker needs `Result`."""
    table = GenericEnumTable()
    table.by_name["Result<i32, StdError>"] = _FakeEnum()
    table.by_name["Maybe"] = _FakeEnum()
    assert enum_base_names(table) == {"Result", "Maybe"}


def test_several_tables_are_unioned():
    concrete = GenericEnumTable()
    concrete.by_name["Color"] = _FakeEnum()
    generic = GenericEnumTable()
    generic.by_name["Box<i32>"] = _FakeEnum()
    assert enum_base_names(concrete, generic) == {"Color", "Box"}


def test_a_plain_mapping_is_accepted_as_itself():
    """A raw dict has no `by_name` and IS the mapping -- the case the `or`-chain was
    reaching for, and the only one it got right."""
    assert enum_base_names({"Shape": _FakeEnum()}) == {"Shape"}
