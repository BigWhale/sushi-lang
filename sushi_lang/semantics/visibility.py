"""Who may name what: the one answer, for every kind of declaration.

`public` used to reach one declaration out of six, so the rule needed one gate, and that
gate could only speak about functions. `docs/design/visibility.md` gives the other five a
marker, which means the gate has to say which KIND of thing it refused, and point at the
declaration that refused it.

One record and one predicate, so a new declaration kind cannot get half the rule. The
record is built from whatever the collect pass holds -- a `FuncSig`, a `ConstSig`, a
manifest entry -- because each of those already carries the same four facts.

A name with NO record is public. That is not a shortcut: the compiler synthesizes types
nothing declared (a monomorphized instance, a lifted closure environment, `FileMode`), and
none of them can carry a source marker. The existing gate already read a missing origin as
permission, and this keeps that reading.

A `DeclOrigin` is read from either of two places, and that is deliberate rather than
duplication. A function and a constant are answered from the collected record, because the
symbol table's own first-writer-wins rule decides WHICH declaration answers a call and the
origin has to be that one. A struct, an enum and a perk are answered from
`VisibilityTable`, because their symbol tables carry a file but no unit and no marker.
Either way the rule is one function, `_permitted`, and the record is one dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AbstractSet, Any, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span


# Every kind `semantics/ast_walk.declarations()` yields, classified by where its answer
# comes from. `docs/design/visibility.md` rules on all four groups, and
# `tests/unit/test_visibility_seam_is_total.py` asserts the union is the whole walk, so a
# new declaration kind cannot get half the rule.
CARRIES_MARKER = frozenset({"constant", "struct", "enum", "perk", "function"})

# As visible as the declaration it is part of. A private enum variant would make a total
# `match` unwritable across a unit boundary, so exhaustiveness decides this one.
FOLLOWS_DECLARATION = frozenset({"field", "variant", "perk method"})

# As visible as the type it is attached to (Ruling 2). Self-enforcing: a private type
# cannot be named, constructed or received elsewhere, so its methods are unreachable
# already, and method resolution stays blind to the caller.
FOLLOWS_TARGET_TYPE = frozenset({"extension", "perk implementation"})

# No visibility at all. An `unsafe external` block is a unit's private implementation
# detail by construction -- `ptr` is quarantined and CE5008 stops one crossing a boundary.
NO_VISIBILITY = frozenset({"external block", "external declaration"})


# Which kinds still read PUBLIC when they carry no marker. `docs/design/visibility.md`
# Ruling 1 makes private the default for all of them, and Phase 2 empties this set one
# kind at a time, so the marker parses and is recorded from the grammar commit on while
# each flip stays one line with a batch of its own.
UNMARKED_IS_PUBLIC = frozenset({"perk"})


def declared_public(kind: str, marked: bool) -> bool:
    """Is a declaration of this kind public, given whether it carries the marker?"""
    return marked or kind in UNMARKED_IS_PUBLIC


# The verb a diagnostic uses for each kind. Derived rather than passed, so a kind cannot
# be given the wrong one at one call site out of four.
_VERB = {
    "function": "call",
    "generic_function": "call",
    "constant": "read",
}
_DEFAULT_VERB = "use"


@dataclass(frozen=True)
class DeclOrigin:
    """Where a declaration came from, and whether it says `public`.

    `unit_name` is None for a name the compiler synthesized or a stdlib symbol with no
    declaring unit. `filename` and `name_span` are what the note points at, and either may
    be absent -- a library names what it keeps without shipping a span for it.
    """

    kind: str
    name: str
    unit_name: Optional[str] = None
    filename: Optional[str] = None
    name_span: Optional[Span] = None
    is_public: bool = True

    @property
    def verb(self) -> str:
        return _VERB.get(self.kind, _DEFAULT_VERB)


def origin_of(kind: str, record: Any) -> DeclOrigin:
    """A `DeclOrigin` from any collected record that carries the same four facts.

    `FuncSig`, `GenericFuncDef` and `ConstSig` are different classes with the same shape
    here, and reading them by attribute keeps this module from importing the collect pass.
    """
    return DeclOrigin(
        kind=kind,
        name=getattr(record, "name", ""),
        unit_name=getattr(record, "unit_name", None),
        filename=getattr(record, "filename", None),
        name_span=getattr(record, "name_span", None),
        is_public=getattr(record, "is_public", True),
    )


@dataclass
class VisibilityTable:
    """Every declaration the collect pass saw, keyed by kind and name.

    One table rather than a visibility side-car per symbol table: `StructTable` and its
    peers already carry `spans` and `files` side-cars, and following that pattern would
    mean eight more dicts across four tables, each a place for the answer to drift.
    """

    by_key: dict[tuple[str, str], DeclOrigin] = field(default_factory=dict)

    # The units that declared a name the table had already taken. The winner is in
    # `by_key`; these are the losers, and each of them has heard why it lost (CE0101,
    # CE0004, CE0006, CE3011). No rule may then measure a loser's own code against the
    # winner's declaration, which is the whole of the D2 cascade family.
    contested: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def record(self, origin: DeclOrigin) -> None:
        """Remember a declaration. The FIRST one wins, as every symbol table does.

        A duplicate is a separate error with its own diagnostic (CE0004, CE0101, CE0105),
        and answering visibility from the first declaration matches what the symbol tables
        themselves kept. The loser is remembered too, because a unit that declared a name
        must not then be told the name is somebody else's.
        """
        key = (origin.kind, origin.name)
        kept = self.by_key.get(key)
        if kept is not None:
            # Only a loss to ANOTHER unit is contested. Recording the same unit again is
            # either the same declaration replayed -- the collect pass builds one table
            # and the merger replays it per unit -- or a duplicate inside one unit, which
            # CE0101 already answers with no other unit to name.
            if origin.unit_name is not None and origin.unit_name != kept.unit_name:
                self.contested.setdefault(key, set()).add(origin.unit_name)
            return
        self.by_key[key] = origin

    def mark_contested(self, kind: str, name: str, unit: Optional[str]) -> None:
        """Book `unit` as a loser of this name, without a declaration to read from.

        The merge uses it: a consumer's declaration replaces a library's export
        (decision 10), so the library unit is now the loser of a name it declared, and
        every rule that would measure the library's own body against the consumer's
        declaration has to know.
        """
        if unit is None:
            return
        self.contested.setdefault((kind, name), set()).add(unit)

    def origin(self, kind: str, name: str) -> Optional[DeclOrigin]:
        return self.by_key.get((kind, name))

    def contested_by(self, kind: str, name: str, unit: Optional[str]) -> bool:
        """Did `unit` declare this name and LOSE it to another unit's declaration?

        Only the loser. A unit that declared the name and WON reads its own record, which
        is trustworthy, and every ordinary rule may measure its code against it.
        """
        if unit is None:
            return False
        return unit in self.contested.get((kind, name), ())

    def is_visible_from(self, kind: str, name: str, unit: Optional[str]) -> bool:
        """May `unit` name this declaration? An unrecorded name always may."""
        origin = self.origin(kind, name)
        if origin is None:
            return True
        return _permitted(origin, unit)


def _permitted(origin: DeclOrigin, current_unit: Optional[str]) -> bool:
    """The whole rule, in one place.

    Each escape is load-bearing. No declaring unit means nothing declared it in source. No
    current unit means the caller is a scratch validator with no unit of its own. And the
    same unit may always name itself.
    """
    if origin.is_public:
        return True
    if origin.unit_name is None or current_unit is None:
        return True
    return origin.unit_name == current_unit


def reject_private_cross_unit_use(
    reporter: Reporter,
    origin: DeclOrigin,
    loc: Any,
    *,
    current_unit: Optional[str],
    table: Optional[VisibilityTable] = None,
    in_library_body: bool = False,
) -> bool:
    """Refuse a use of another unit's private declaration. True when it was refused.

    `in_library_body` is the one caller-side escape: a library body transplanted into the
    consumer's compile may call the library's own privates, and the code the user wrote may
    not (#468).

    `table` is the second escape: a unit that declared this name ITSELF has already been
    told that its declaration lost, so telling it the name is private somewhere else is
    the cascade and not the diagnosis (D2 shapes a and b).
    """
    if in_library_body:
        return False
    if table is not None and table.contested_by(origin.kind, origin.name, current_unit):
        return False
    if _permitted(origin, current_unit):
        return False

    diagnostic = er.emit_with(
        reporter, er.ERR.CE3005, loc,
        verb=origin.verb, kind=origin.kind, name=origin.name,
        current_unit=current_unit, owner=origin.unit_name,
    )
    # BOTH, not either: the collect pass walks every unit through one reporter, so a span
    # with no file of its own renders against whichever file the reporter is pointing at
    # (#473). A record that cannot say where it lives gets the head line alone.
    if origin.name_span is not None and origin.filename is not None:
        diagnostic = diagnostic.note(
            "declared here, without `public`", origin.name_span, origin.filename)
    diagnostic.emit()
    return True


def library_clash_origin(
    table: Optional[VisibilityTable],
    kind: str,
    name: str,
    *,
    current_unit: Optional[str],
    library_units: AbstractSet[str],
) -> Optional[DeclOrigin]:
    """The LIBRARY declaration a consumer's own declaration collides with, or None.

    A consumer cannot win a name a source library or a bundled stdlib module already
    declared. There used to be a shadow branch that let it try; it deleted the library's
    entry and registered no replacement, so the consumer lost its own declaration too.
    Completing it is not safe either, because one namespace means the library's own bodies
    would then call the consumer's function. CE3011 refuses it instead.

    The answer is read from this table rather than from each symbol table, because a
    struct table carries a file and not a unit. The table is filled at the END of each
    unit's collection, so a name the CURRENT unit declared twice is absent here and stays
    an ordinary duplicate.
    """
    if table is None or current_unit is None or current_unit in library_units:
        return None
    origin = table.origin(kind, name)
    if origin is None or origin.unit_name is None:
        return None
    if origin.unit_name not in library_units:
        return None
    return origin


def library_clash_for_type_name(
    table: Optional[VisibilityTable],
    name: str,
    *,
    current_unit: Optional[str],
    library_units: AbstractSet[str],
) -> Optional[DeclOrigin]:
    """The library STRUCT or ENUM a consumer's type name collides with, or None.

    One namespace holds both kinds, so a consumer's `enum Mood` loses to a library's
    `struct Mood` exactly as it loses to a library's `enum Mood`.
    """
    for kind in ("struct", "enum"):
        origin = library_clash_origin(table, kind, name, current_unit=current_unit,
                                      library_units=library_units)
        if origin is not None:
            return origin
    return None


def reject_library_clash(
    reporter: Reporter,
    origin: DeclOrigin,
    loc: Any,
    *,
    kind: str,
    name: str,
    filename: Optional[str],
) -> None:
    """CE3011 at the consumer's declaration, with a note at the library's."""
    diagnostic = er.emit_with(
        reporter, er.ERR.CE3011, loc,
        filename=filename, kind=kind, name=name, owner=origin.unit_name,
    )
    if origin.name_span is not None and origin.filename is not None:
        diagnostic = diagnostic.note("declared here", origin.name_span, origin.filename)
    diagnostic.emit()


def record_declarations(
    table: VisibilityTable,
    program: Any,
    *,
    unit_name: Optional[str],
    filename: Optional[str],
) -> None:
    """Record one unit's declarations, for the kinds that carry a marker.

    Driven by `declarations()` rather than by the symbol tables, so the walk that is
    already gated for totality is what decides the list.
    """
    from sushi_lang.semantics.ast_walk import declarations

    for kind, node in declarations(program):
        if kind not in CARRIES_MARKER:
            continue
        name = getattr(node, "name", None)
        if not isinstance(name, str):
            continue
        table.record(DeclOrigin(
            kind=kind,
            name=name,
            unit_name=unit_name,
            filename=filename,
            name_span=getattr(node, "name_span", None) or getattr(node, "loc", None),
            is_public=getattr(node, "is_public", True),
        ))
