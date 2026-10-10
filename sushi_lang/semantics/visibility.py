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

The table is FILLED by the collectors: each files the kinds it collects, at the
declaration and before any refusal, so a duplicate the collector turns away is still
remembered as contested. There used to be a seventh walk over the declarations, after the
six collectors, that recorded the same four facts a second time (#691).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import AbstractSet, Any, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span


# An extension method or a static method (`docs/design/extension-visibility.md` R4 to
# R7) carries its own marker. A conversion (`extend A as B:`) is written with `extend`
# too, but it carries no marker and travels with its target (R8), so it is a kind of its
# own. The declaration walk yields one of the two words for each `extend` block.
EXTENSION_METHOD = "extension method"
CONVERSION = "conversion"

# Every kind `semantics/ast_walk.declarations()` yields, classified by where its answer
# comes from. `docs/design/visibility.md` rules on all four groups, and
# `tests/unit/test_visibility_seam_is_total.py` asserts the union is the whole walk, so a
# new declaration kind cannot get half the rule.
CARRIES_MARKER = frozenset({"constant", "variable", "struct", "enum", "perk", "function",
                            EXTENSION_METHOD})

# As visible as the declaration it is part of. A private enum variant would make a total
# `match` unwritable across a unit boundary, so exhaustiveness decides this one.
FOLLOWS_DECLARATION = frozenset({"field", "variant", "perk method"})

# As visible as the type it is attached to (Ruling 2). Self-enforcing: a private type
# cannot be named, constructed or received elsewhere, so its methods are unreachable
# already. A perk implementation is global and unique (R8), and a conversion can be
# declared only in the home unit of its target.
FOLLOWS_TARGET_TYPE = frozenset({CONVERSION, "perk implementation"})

# No visibility at all. An `unsafe external` block is a unit's private implementation
# detail by construction -- `ptr` is quarantined and CE5008 stops one crossing a boundary.
NO_VISIBILITY = frozenset({"external block", "external declaration"})


def declared_public(kind: str, marked: bool) -> bool:
    """Is a declaration of this kind public, given whether it carries the marker?

    The marker alone answers, for every kind in `CARRIES_MARKER`. That is Ruling 1 and
    Ruling 3 in full: private is the default, and no kind is public without the word.
    A `frozenset` of kinds that are public when unmarked stood here and was empty, so
    the answer was already the marker. The gate is
    `tests/unit/test_public_marker_is_recorded.py`.
    """
    return marked


# The verb a diagnostic uses for each kind. Derived rather than passed, so a kind cannot
# be given the wrong one at one call site out of four.
_VERB = {
    "function": "call",
    "generic_function": "call",
    EXTENSION_METHOD: "call",
    "constant": "read",
    "variable": "read",
}
_DEFAULT_VERB = "use"
# The verb of a function taken as a VALUE, not called (#1013). The position knows it and
# the kind does not, so the caller passes it.
VALUE_VERB = "take the value of"


def kind_word(kind: str, decl: Any) -> str:
    """The word a diagnostic calls a declaration of this kind by.

    An `error` declaration is an enum with a flag (docs/design/error-conversion.md
    section 2.2). Its kind KEY stays "enum" for every rule; only the word a reader sees
    changes. `decl` is anything that carries the flag: an `EnumDef`, an `EnumType`, a
    generic template or a `DeclOrigin`.
    """
    if kind == "enum" and getattr(decl, "is_error", False):
        return "error type"
    return kind


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
    is_error: bool = False

    @property
    def verb(self) -> str:
        return _VERB.get(self.kind, _DEFAULT_VERB)

    @property
    def word(self) -> str:
        return kind_word(self.kind, self)


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


# The two kinds that share one TYPE name for the whole program.
TYPE_KINDS: tuple[str, ...] = ("struct", "enum")


def _other_type_kind(kind: str) -> str:
    return "enum" if kind == "struct" else "struct"


@dataclass
class VisibilityTable:
    """Every declaration the collect pass saw, keyed by kind and name.

    One table rather than a visibility side-car per symbol table: `StructTable` and its
    peers already carry `spans` and `files` side-cars, and following that pattern would
    mean eight more dicts across four tables, each a place for the answer to drift.
    """

    by_key: dict[tuple[str, str], DeclOrigin] = field(default_factory=dict)

    # Every LATER declaration of a name the table had already taken, in the order the
    # collect pass saw them. The winner is in `by_key`, and no rule may measure a loser's
    # own code against it -- the whole of the D2 cascade family. A TYPE loser has heard
    # why it lost (CE0004, CE0006, CE3011). A function or a constant loses only this
    # table's answer now, not the name: each takes its own `<unit>$<name>` symbol and its
    # own entry in `by_unit`, which is what its own code is measured against
    # (`docs/design/unit-namespaces.md` section 9).
    #
    # The ORIGIN is kept and not just the unit name, because `CE3012` names every
    # candidate a written name could mean and points at each declaration.
    contested: dict[tuple[str, str], list[DeclOrigin]] = field(default_factory=dict)

    # The names a unit declared a second time under another kind, keyed (unit, name).
    # CE1005 or CE0006 refused that declaration, and a use that finds nothing under the
    # name is the same fault, so it gives no second diagnostic (#1102).
    refused: set[tuple[Optional[str], str]] = field(default_factory=set)

    def refused_by(self, unit: Optional[str], name: str) -> bool:
        """Did `unit` declare `name` a second time, and lose the declaration?"""
        return (unit, name) in self.refused

    def record(self, origin: DeclOrigin) -> None:
        """Remember a declaration. The FIRST one wins, as every symbol table does.

        A duplicate is a separate error with its own diagnostic (CE0004, CE0101, CE0105),
        and answering visibility from the first declaration matches what the symbol tables
        themselves kept. The loser is remembered too, because a unit that declared a name
        must not then be told the name is somebody else's.
        """
        key = (origin.kind, origin.name)
        kept = self.by_key.get(key)
        if kept is None and origin.kind in TYPE_KINDS:
            # A struct and an enum share ONE name: a type another unit already took
            # under the other kind is a loss too (CE0006, CE3011), and the loser does
            # not take the winner's slot of its own kind (#814).
            sibling = self.by_key.get((_other_type_kind(origin.kind), origin.name))
            if sibling is not None and origin.unit_name is not None \
                    and origin.unit_name != sibling.unit_name:
                kept = sibling
        if kept is not None:
            # Only a loss to ANOTHER unit is contested. Recording the same unit again is
            # either the same declaration replayed -- the collect pass builds one table
            # and the merger replays it per unit -- or a duplicate inside one unit, which
            # CE0101 already answers with no other unit to name.
            if origin.unit_name is not None and origin.unit_name != kept.unit_name:
                seen = self.contested.setdefault(key, [])
                if not any(other.unit_name == origin.unit_name for other in seen):
                    seen.append(origin)
            return
        self.by_key[key] = origin

    def origin(self, kind: str, name: str) -> Optional[DeclOrigin]:
        return self.by_key.get((kind, name))

    def origins(self, kind: str, name: str) -> list[DeclOrigin]:
        """EVERY declaration of this name the collect pass saw, the winner first.

        A rule that asks "may this unit write the name at all" has to read all of
        them: the winner belongs to whichever unit was collected first, and a name is
        writable when ANY declaration of it is reachable.
        """
        winner = self.by_key.get((kind, name))
        if winner is None:
            return []
        return [winner, *self.contested.get((kind, name), ())]

    def contested_by(self, kind: str, name: str, unit: Optional[str]) -> bool:
        """Did `unit` declare this name and LOSE it to another unit's declaration?

        Only the loser. A unit that declared the name and WON reads its own record, which
        is trustworthy, and every ordinary rule may measure its code against it.
        """
        if unit is None:
            return False
        kinds = TYPE_KINDS if kind in TYPE_KINDS else (kind,)
        return any(other.unit_name == unit
                   for each in kinds
                   for other in self.contested.get((each, name), ()))

    def candidates(self, kind: str, name: str, unit: Optional[str],
                   scope: Any = None) -> list[DeclOrigin]:
        """Every declaration of this name that `unit` may name, its own excluded.

        The answer to "what could an unqualified name mean here". Two or more is
        `CE3012` (`docs/design/unit-namespaces.md` section 6): the asking unit's own
        declaration wins outright, and a private one next door is not a candidate at
        all -- it is not nameable, so it cannot be part of an ambiguity.

        `scope` narrows the same predicate to the units this one imported. A name a
        unit next door imported was a candidate while the flat scope was the whole
        program, and stops being one when the scope stops at the import.
        """
        found = self.origins(kind, name)
        return [origin for origin in found
                if origin.unit_name != unit and _permitted(origin, unit)
                and (scope is None or scope.holds_unit(origin.unit_name))]

    def is_ambiguous(self, kind: str, name: str, unit: Optional[str],
                     scope: Any = None) -> bool:
        """Do two or more `candidates` offer this name to `unit`, and `unit` declares none?

        Row 3 of the ladder in section 8 of `docs/design/unit-namespaces.md`: an own
        declaration of `unit` answers first, and two imported candidates resolve to
        nothing. A reader that would take the type of the first candidate asks this
        first, so that one fault gives one diagnostic.
        """
        if any(origin.unit_name == unit for origin in self.origins(kind, name)):
            return False
        return len(self.candidates(kind, name, unit, scope)) >= 2

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
    verb: Optional[str] = None,
) -> bool:
    """Refuse a use of another unit's private declaration. True when it was refused.

    `verb` overrides the kind's own verb for a position the kind cannot know: a function
    taken as a value is not called (`VALUE_VERB`).

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
        verb=verb or origin.verb, kind=origin.word, name=origin.name,
        current_unit=current_unit, owner=origin.unit_name,
    )
    # BOTH, not either: the collect pass walks every unit through one reporter, so a span
    # with no file of its own renders against whichever file the reporter is pointing at
    # (#473). A record that cannot say where it lives gets the head line alone.
    if origin.name_span is not None and origin.filename is not None:
        diagnostic = diagnostic.note_at(
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

    Two callers read this, and each reads it for its own kind. A TYPE the library holds
    is a name the consumer cannot declare again, because identity is nominal and one name
    is one shape: CE3011 refuses it. A FUNCTION is only asked about here to see whether
    the library EXPORTS the name, which is the CW3002 warning; a library's private
    function coexists with the consumer's, because each carries the unit that declared it.

    There used to be a shadow branch that let a consumer take a library's name; it deleted
    the library's entry and registered no replacement, so the consumer lost its own
    declaration too.

    The answer is read from this table rather than from each symbol table, because a
    struct table carries a file and not a unit. The table is filled at the END of each
    unit's collection, so a name the CURRENT unit declared twice is absent here and stays
    an ordinary duplicate.
    """
    if table is None:
        return None
    origin = table.origin(kind, name)
    if origin is None or not taken_by_a_library(
            origin.unit_name, current_unit=current_unit, library_units=library_units):
        return None
    return origin


def warn_shadowed_export(
    reporter: Reporter,
    name: str,
    name_span: Optional[Span],
    filename: Optional[str],
    *,
    owner: Optional[str],
    export_span: Optional[Span] = None,
    export_filename: Optional[str] = None,
) -> None:
    """CW3002: a consumer function takes the name of a function that a library exports.

    The one emitter for every library kind (#1103). The collect pass calls it for a
    source library, and the `libraries` step calls it for a binary or a hybrid
    library. A manifest record has no span, so its warning has no note.
    """
    diagnostic = er.emit_with(reporter, er.ERR.CW3002, name_span, filename=filename,
                              name=name, kind="function", owner=owner)
    if export_span is not None and export_filename is not None:
        diagnostic = diagnostic.note_at("exported here", export_span, export_filename)
    diagnostic.emit()


def taken_by_a_library(
    owner_unit: Optional[str],
    *,
    current_unit: Optional[str],
    library_units: AbstractSet[str],
) -> bool:
    """Is `owner_unit` a library's, and `current_unit` a consumer's?

    The one predicate under every "did a library already take this" question: a name in
    this table, and a perk implementation the implementation table owns, which has no
    record here because it carries no marker (`FOLLOWS_TARGET_TYPE`). A library unit
    collected beside another library's declaration is not a consumer of it.
    """
    if current_unit is None or current_unit in library_units:
        return False
    return owner_unit is not None and owner_unit in library_units


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
    return _library_clash_for_kinds(("struct", "enum"), table, name,
                                    current_unit=current_unit,
                                    library_units=library_units)


def library_clash_for_storage_name(
    table: Optional[VisibilityTable],
    name: str,
    *,
    current_unit: Optional[str],
    library_units: AbstractSet[str],
) -> Optional[DeclOrigin]:
    """The library CONSTANT or `var` a consumer's constant or `var` collides with, or None.

    One table holds both kinds of unit-level storage, so a consumer's `var LIMIT` takes
    a library's `const LIMIT` exactly as another constant would (#691).
    """
    return _library_clash_for_kinds(("constant", "variable"), table, name,
                                    current_unit=current_unit,
                                    library_units=library_units)


def _library_clash_for_kinds(
    kinds: tuple[str, ...],
    table: Optional[VisibilityTable],
    name: str,
    *,
    current_unit: Optional[str],
    library_units: AbstractSet[str],
) -> Optional[DeclOrigin]:
    for kind in kinds:
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
        diagnostic = diagnostic.note_at("declared here", origin.name_span, origin.filename)
    diagnostic.emit()


def reject_private_perk_contract(
    reporter: Reporter,
    table: Optional[VisibilityTable],
    name: str,
    loc: Any,
    *,
    action: str,
    current_unit: Optional[str],
    filename: Optional[str],
) -> bool:
    """Refuse a promise about another unit's private perk (CE4011). True when refused.

    Two use sites, one rule: implementing the perk, and constraining a type parameter
    with it. Both are statements about the contract, which is what a private perk keeps.
    Calling a method the perk provides is neither, and stays legal -- Ruling 3.
    """
    if table is None:
        return False
    origin = table.origin("perk", name)
    if origin is None or _permitted(origin, current_unit):
        return False
    diagnostic = er.emit_with(
        reporter, er.ERR.CE4011, loc,
        filename=filename, action=action, name=name,
        current_unit=current_unit, owner=origin.unit_name,
    )
    if origin.name_span is not None and origin.filename is not None:
        diagnostic = diagnostic.note_at(
            "declared here, without `public`", origin.name_span, origin.filename)
    diagnostic.emit()
    return True


def reject_private_perk_constraints(
    reporter: Reporter,
    table: Optional[VisibilityTable],
    program: Any,
    *,
    current_unit: Optional[str],
    filename: Optional[str],
) -> None:
    """Every constraint one unit writes, against the use-site rule (CE4011).

    Driven by `signature_constraints()`, the one walk over a unit's constraint names, so
    the four kinds that carry a type parameter -- a function, a struct, an enum and an
    extension -- meet one call and not one call site each. Three copied call sites left
    the extension out, and its constraint fell through to the leak rule (#692).
    """
    from sushi_lang.semantics.ast_walk import signature_constraints

    for site in signature_constraints(program):
        reject_private_perk_contract(
            reporter, table, site.perk_name, site.span,
            action="constrain a type parameter with",
            current_unit=current_unit, filename=filename)


def extension_method_name(target: str, method: str) -> str:
    """The name an extension method is filed under: its target, then its own name.

    The method name alone is not unique, because many targets can declare one method
    name. The key `(EXTENSION_METHOD, "i32.twice")` holds one method on one target.
    """
    return f"{target}.{method}"


def record_declaration(
    table: Optional[VisibilityTable],
    kind: str,
    node: Any,
    *,
    unit_name: Optional[str],
    filename: Optional[str],
    name: Optional[str] = None,
) -> None:
    """File one declaration from the collector that meets it.

    Called at the declaration, before the collector decides whether to keep it: a
    duplicate it refuses is exactly what `contested` has to remember, or the loser is
    later told the name is somebody else's (D2). The same declaration filed twice from
    one unit is a no-op. A collector built without a table -- a throwaway over a
    library snippet -- files nothing, as it recorded nothing before. `name` replaces
    the name of the node where the node name is not the key (`extension_method_name`).
    """
    if table is None or kind not in CARRIES_MARKER:
        return
    if name is None:
        name = getattr(node, "name", None)
    if not isinstance(name, str):
        return
    table.record(DeclOrigin(
        kind=kind,
        name=name,
        unit_name=unit_name,
        filename=filename,
        name_span=getattr(node, "name_span", None) or getattr(node, "loc", None),
        is_public=getattr(node, "is_public", True),
        is_error=getattr(node, "is_error", False),
    ))


# --- Extension methods (`docs/design/extension-visibility.md`) -------------------------
#
# A method is found on the type of its receiver, so the question "may this unit call it"
# cannot be answered by the flat scope of names. It has its own predicate, with the same
# escapes as `_permitted`, and every caller of method resolution reads it through
# `extension_reach`. The two diagnostics it can cause are in the pass adapter
# (`passes/types/visibility.py`), because only a call site knows its span.


class MethodReach(Enum):
    """What one extension method is to one calling unit."""

    VISIBLE = "visible"
    # Another unit's extension with no marker (R4): CE3005.
    PRIVATE = "private"
    # A public extension on a type that its unit does not declare, and this unit does
    # not import that unit (R6): CE3022.
    NOT_IMPORTED = "not imported"


# The built-in generic types of R1. Their name needs no import, and the stdlib is their
# home. `HashMap@(K, V)` is not one: its name needs `use <collections/hashmap>` (R3).
BUILTIN_GENERIC_BASES = frozenset({"List", "Own", "Maybe", "Result"})


def _base_name(ty: Any) -> Optional[str]:
    """The declared name of a type: the base of a generic, the name of anything else."""
    for attr in ("generic_base", "base_name", "name"):
        name = getattr(ty, attr, None)
        if isinstance(name, str):
            return name
    return None


def is_builtin_type(ty: Any) -> bool:
    """R1: is `ty` a type whose name needs no import, so that the stdlib is its home?

    A primitive, `string`, a fixed or a dynamic array, `List@(T)`, `Own@(T)`,
    `Maybe@(T)` and `Result@(T, E)`.
    """
    from sushi_lang.semantics.typesys import ArrayType, BuiltinType, DynamicArrayType
    if isinstance(ty, (BuiltinType, ArrayType, DynamicArrayType)):
        return True
    return _base_name(ty) in BUILTIN_GENERIC_BASES


def home_unit_of(table: Optional[VisibilityTable], ty: Any) -> Optional[str]:
    """R5: the unit that declares the type `ty`, or None when it has no home unit.

    The home of a generic type is the unit that declares its base. The home of a
    predefined enum is the stdlib module that its stamp names (`FileMode` and
    `<io/fs>`). A built-in type, an array type and a type that no unit declares have no
    home unit.
    """
    from sushi_lang.semantics.typesys import ArrayType, BuiltinType, DynamicArrayType
    if table is None or isinstance(ty, (BuiltinType, ArrayType, DynamicArrayType)):
        return None
    name = _base_name(ty)
    if name is None:
        return None
    for kind in TYPE_KINDS:
        origin = table.origin(kind, name)
        if origin is not None:
            return origin.unit_name
    home_module = getattr(ty, "home_module", None)
    return home_module if isinstance(home_module, str) else None


def extension_reach(table: Optional[VisibilityTable], method: Any, target: Any,
                    asker: Optional[str], scope: Any) -> MethodReach:
    """May the unit `asker` call the extension `method` on a receiver of type `target`?

    The whole rule of `docs/design/extension-visibility.md`, in one place, for an
    instance method and a static method alike (R7). `method` is any record that carries
    `unit_name` and `is_public`. `scope` is the `UnitScope` of the calling unit. A
    record with no unit, and a reader with no unit, take the escapes of `_permitted`.
    """
    declared_in = getattr(method, "unit_name", None)
    if declared_in is None or asker is None or declared_in == asker:
        return MethodReach.VISIBLE
    if not getattr(method, "is_public", False):
        return MethodReach.PRIVATE
    if declared_in == home_unit_of(table, target):
        return MethodReach.VISIBLE
    if is_stdlib_builtin_method(method, target):
        return MethodReach.VISIBLE
    if scope is None or scope.brings_methods_of(declared_in):
        return MethodReach.VISIBLE
    return MethodReach.NOT_IMPORTED


def is_stdlib_builtin_method(method: Any, target: Any) -> bool:
    """R1: is `method` a public extension that a Sushi-source stdlib module declares on a
    built-in type? Such a method is visible in every unit, with no import."""
    from sushi_lang.semantics.stdlib_registry import is_source_stdlib_module
    declared_in = getattr(method, "unit_name", None)
    return (getattr(method, "is_public", False) and declared_in is not None
            and is_source_stdlib_module(declared_in) and is_builtin_type(target))


@dataclass(frozen=True)
class ExtensionClaim:
    """The unit and the marker of an extension declaration that no table holds yet.

    `extensions_collide` reads a record and a claim alike.
    """

    unit_name: Optional[str]
    is_public: bool


def extensions_collide(table: Optional[VisibilityTable], target: Any,
                       first: Any, second: Any) -> bool:
    """Can two extensions of one name on one type not both be declared?

    C1: one unit declares both. C2 for a home unit: one of them is a public extension in
    the home unit of the type, so it is visible wherever the type is (R5), and the other
    could never be called. Any other pair coexists: each unit calls its own (C5), and an
    imported one is the warning or the error of the call (C3, C4).
    """
    if first.unit_name == second.unit_name:
        return True
    home = home_unit_of(table, target)
    return home is not None and any(
        getattr(each, "is_public", False) and each.unit_name == home
        for each in (first, second))


def hides_import(table: Optional[VisibilityTable], own: Any, other: Any,
                 scope: Any) -> bool:
    """C3: does the unit's own extension `own` hide the imported public extension `other`?

    Both are of one name on one type. `other` is visible in the unit of `own` through an
    import alone (R6). A public method of the home unit (R5) and a stdlib method on a
    built-in type (R1) are visible everywhere, and an own extension of their name is an
    error at its declaration (C2), not this warning.
    """
    target = own.target_type
    declared_in = getattr(other, "unit_name", None)
    return (declared_in is not None and declared_in != own.unit_name
            and bool(getattr(other, "is_static", False)) == bool(
                getattr(own, "is_static", False))
            and getattr(other, "is_public", False)
            and declared_in != home_unit_of(table, target)
            and not is_stdlib_builtin_method(other, target)
            and scope is not None and scope.brings_methods_of(declared_in))


def warn_hidden_extension(reporter: Reporter, own: Any, other: Any, name: str) -> None:
    """CW3007 at the own extension, with a note at the imported one it hides (C3).

    `name` is the method as the reader writes it, `<type>.<method>`.
    """
    diagnostic = er.emit_with(reporter, er.ERR.CW3007, own.name_span or own.loc,
                              filename=own.filename, name=name, owner=other.unit_name)
    if other.name_span is not None and other.filename is not None:
        diagnostic = diagnostic.note_at("the imported extension method is declared here",
                                        other.name_span, other.filename)
    diagnostic.help("a call in this unit calls the method of this unit; rename it to "
                    "call the imported one").emit()
