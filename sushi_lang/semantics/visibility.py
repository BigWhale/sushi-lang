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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span


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

    def record(self, origin: DeclOrigin) -> None:
        """Remember a declaration. The FIRST one wins, as every symbol table does.

        A duplicate is a separate error with its own diagnostic (CE0004, CE0101, CE0105),
        and answering visibility from the first declaration matches what the symbol tables
        themselves kept.
        """
        self.by_key.setdefault((origin.kind, origin.name), origin)

    def origin(self, kind: str, name: str) -> Optional[DeclOrigin]:
        return self.by_key.get((kind, name))

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
    in_library_body: bool = False,
) -> bool:
    """Refuse a use of another unit's private declaration. True when it was refused.

    `in_library_body` is the one caller-side escape: a library body transplanted into the
    consumer's compile may call the library's own privates, and the code the user wrote may
    not (#468).
    """
    if in_library_body:
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
