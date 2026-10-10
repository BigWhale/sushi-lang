"""Who may name what: the typecheck pass's view of the visibility seam.

The rule itself lives in `semantics/visibility.py`, which answers for every kind of
declaration. This module is the pass's adapter: it holds the two things only a use site
knows -- the validator it is running inside, and that a transplanted library body is
allowed to call its own library's privates (#468). A call and a bare constant read ask
the same two questions, so they ask them here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AbstractSet, Any, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.namespaces import (
    GENERIC_UNIT_TYPES,
    import_help,
    import_target,
)
from sushi_lang.semantics.typesys import EnumType, StructType
from sushi_lang.semantics.visibility import (
    EXTENSION_METHOD,
    DeclOrigin,
    MethodReach,
    extension_reach,
    origin_of,
    reject_private_cross_unit_use,
)

if TYPE_CHECKING:
    from . import TypeValidator

__all__ = ["calling_unit", "extension_reaches", "name_is_ambiguous",
           "name_is_contested", "out_of_scope_help",
           "reject_ambiguous_extension", "reject_ambiguous_name",
           "reject_unreachable_extension",
           "reject_out_of_scope_perk", "reject_out_of_scope_type",
           "reject_private_call", "reject_private_kept",
           "reject_private_kept_call", "reject_private_name",
           "reject_private_type", "type_is_contested", "type_name_is_contested",
           "name_was_refused"]


# Which kinds one written type name could be. `struct` and `enum` share one namespace,
# and a perk is here because a perk written in a type position is that same mistake. A
# perk in a perk position asks `reject_out_of_scope_perk`.
_TYPE_KINDS = ("struct", "enum", "perk")


def reject_out_of_scope_type(validator: 'TypeValidator', name: str,
                             loc: Any) -> bool:
    """CE2001: a type some unit declares and THIS unit did not import (section 6.1).

    A public signature may name a type its caller cannot name, and the caller must add
    the import. The refusal is `CE2001` because that is what it is -- the name is not a
    type here -- and the help line is what says where the type is.

    A name is writable when ANY declaration of it is reachable, so every origin is read
    and not just the winner: a unit that declares the name itself may always write it.
    """
    if getattr(validator, "in_synthesized_body", False):
        return False
    scope = validator.scope
    if not scope.holds_generic(name):
        module = next((path for path, generic in GENERIC_UNIT_TYPES.items()
                       if generic == name), None)
        _reject_unreachable(validator, name, loc,
                            import_help(module, stdlib=True) if module else None)
        return True

    # A predefined enum is gated by its HOME module's import (#574, Ruling 3), the same
    # rule as the generic above: the stamp says which import, and the help names it.
    home = getattr(validator.enum_table.by_name.get(name), "home_module", None)
    if home is not None and not scope.holds_home(home):
        _reject_unreachable(validator, name, loc, import_help(home, stdlib=True))
        return True

    declarer = _unreachable_declarer(validator, _TYPE_KINDS, name)
    if declarer is None:
        return False
    _reject_unreachable(validator, name, loc,
                        import_help(declarer, tables=_unit_tables(validator)))
    return True


def _unreachable_declarer(validator: 'TypeValidator', kinds: tuple[str, ...],
                          name: str) -> Optional[str]:
    """The unit to import when units declare `name` and none is in this unit's scope.

    None when the name has no record of these kinds, or when one record is reachable.
    """
    table = getattr(validator, "visibility", None)
    if table is None:
        return None
    origins = [origin for kind in kinds
               for origin in table.origins(kind, name)
               if origin.unit_name is not None]
    if not origins or any(validator.scope.holds_unit(o.unit_name) for o in origins):
        return None
    return origins[0].unit_name


def reject_out_of_scope_perk(validator: 'TypeValidator', name: str, loc: Any) -> bool:
    """CE4003: a perk some unit declares and THIS unit cannot name (#1124).

    One rule for every perk position -- an implementation, a constraint, a pack
    constraint -- and the scope is the one the other names read. A perk that a compiled
    library ships has no visibility record, so its `lib/<library>/<unit>` key is asked
    instead. A predefined perk has neither, and is in every scope.
    """
    if getattr(validator, "in_synthesized_body", False) or validator.in_library_unit:
        return False
    declarer = _unreachable_declarer(validator, ("perk",), name)
    if declarer is None:
        library_unit = validator.perk_table.library_units.get(name)
        if library_unit is None or validator.scope.holds_unit(library_unit):
            return False
        declarer = library_unit
    er.emit_with(validator.reporter, er.ERR.CE4003, loc, perk=name) \
        .help(import_help(declarer, tables=_unit_tables(validator))).emit()
    return True


def _unit_tables(validator: 'TypeValidator') -> Any:
    """Every unit's namespace table, which says whether a unit is a library's."""
    return getattr(getattr(validator, "tables", None), "namespaces", None)


def _reject_unreachable(validator: 'TypeValidator', name: str, loc: Any,
                        help_line: Optional[str]) -> None:
    """CE2001 for a name that exists and cannot be written here."""
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2001, loc, name=name)
    if help_line is not None:
        diagnostic = diagnostic.help(help_line)
    diagnostic.emit()


def out_of_scope_help(validator: 'TypeValidator', kind: str,
                      name: str) -> Optional[str]:
    """The help line for a name some unit declares and THIS unit did not import.

    The scope seam's question at a use site, and the sibling of every rule below: both
    live here because both need the validator, and neither may answer for the other. A
    name refused for being out of scope is refused as "no such name", so this line is
    the only thing that says where the name is.
    """
    table = getattr(validator, "visibility", None)
    if table is not None:
        for origin in table.candidates(kind, name, validator.current_unit_name):
            if (origin.unit_name is not None
                    and not validator.scope.holds_unit(origin.unit_name)):
                return import_help(origin.unit_name, tables=_unit_tables(validator))
    if kind != "function":
        return None
    # A compiled library's function has no visibility record: its unit-keyed entry
    # says which library unit declares it (#1120).
    tables = _unit_tables(validator) or {}
    for by_unit in (validator.func_table.by_unit, validator.generic_func_table.by_unit):
        for unit_name, declared in by_unit.items():
            sig = declared.get(name)
            if (sig is not None and getattr(sig, "is_public", False)
                    and getattr(tables.get(unit_name), "library", None) is not None
                    and not validator.scope.holds_unit(unit_name)):
                return import_help(unit_name, tables=tables)
    found = validator.func_table.lookup_stdlib_by_name(name)
    if found is not None and not validator.scope.holds_module(found[0]):
        return import_help(found[0], stdlib=True)
    return None


def name_is_contested(validator: 'TypeValidator', kind: str, name: str) -> bool:
    """Did the unit being validated declare this name and LOSE it?

    A contested name has no trustworthy declaration for the unit that lost it: the table
    holds somebody else's, and the loser has already heard why (CE0101, CE0004, CE2046,
    CE3011). Every rule that reads the winner's record asks this first, or the loser is
    shown its own code measured against a declaration it never wrote (D2).
    """
    table = getattr(validator, "visibility", None)
    if table is None:
        return False
    return table.contested_by(kind, name, validator.current_unit_name)


def name_was_refused(validator: 'TypeValidator', name: str) -> bool:
    """Did the unit being validated declare `name` a second time, under another kind?

    CE1005 (or CE0006) refused that declaration, so a use of the name that finds
    nothing is the same fault and gives no second diagnostic (#1102).
    """
    table = getattr(validator, "visibility", None)
    return table is not None and table.refused_by(validator.current_unit_name, name)


def type_name_is_contested(validator: 'TypeValidator', name: str) -> bool:
    """Did the unit being validated declare the TYPE `name` and lose it?

    A struct and an enum share one name, so the kinds of the loser and the winner do
    not matter: the unit that lost the name has heard CE0004, CE0006 or CE3011 at its
    declaration, and every use of the name in that unit is silent (#921).
    """
    return name_is_contested(validator, "struct", name)


def type_is_contested(validator: 'TypeValidator', ty: Any) -> bool:
    """Is `ty` a named type whose name the unit being validated declared and lost?"""
    if not isinstance(ty, (StructType, EnumType)):
        return False
    return type_name_is_contested(validator, ty.generic_base or ty.name)


def name_is_ambiguous(validator: 'TypeValidator', kind: str, name: str) -> bool:
    """Does the name reach two or more imported candidates and no own declaration?

    Such a name has no type in the unit being validated. The inference asks this, so
    that no check measures the use against the first candidate.
    """
    table = getattr(validator, "visibility", None)
    return table is not None and table.is_ambiguous(
        kind, name, validator.current_unit_name, validator.scope)


def reject_ambiguous_name(validator: 'TypeValidator', kind: str, name: str,
                          loc: Any) -> bool:
    """CE3012: more than one unit in scope offers this name. True when it was refused.

    Section 6 of `docs/design/unit-namespaces.md`. The refusal stands at the USE, where
    the choice was not made, and it names every candidate. CE3003 refused the whole
    program instead, for a collision that might never be written. The question is
    `is_ambiguous`, so an own declaration of the unit wins before the candidates count.
    """
    if not name_is_ambiguous(validator, kind, name):
        return False
    table = validator.visibility
    candidates = table.candidates(kind, name, validator.current_unit_name,
                                  validator.scope)

    diagnostic = er.emit_with(validator.reporter, er.ERR.CE3012, loc, name=name)
    for origin in candidates:
        if origin.name_span is not None:
            diagnostic = diagnostic.note_at(
                f"unit '{origin.unit_name}' declares it here",
                origin.name_span, origin.filename)
    first = import_target(candidates[0].unit_name, tables=_unit_tables(validator))
    diagnostic.help(f"say which one: `use {first} as u` above, then `u.{name}`").emit()
    return True


def _reject(validator: 'TypeValidator', origin: DeclOrigin, loc: Any,
            verb: Optional[str] = None) -> bool:
    return reject_private_cross_unit_use(
        validator.reporter, origin, loc,
        current_unit=validator.current_unit_name,
        table=getattr(validator, "visibility", None),
        in_library_body=bool(getattr(validator, "in_library_body", False)),
        verb=verb,
    )


def reject_private_call(validator: 'TypeValidator', kind: str, sig: Any, loc: Any) -> bool:
    """Reject a call to a private function of another unit. True when it was rejected."""
    return _reject(validator, origin_of(kind, sig), loc)


def reject_private_kept_call(
    validator: 'TypeValidator', name: str, loc: Any,
    *, library: str, kind: Optional[str],
) -> bool:
    """Reject a call to a name a LIBRARY declares and does not export (#469).

    No signature travels with a kept name, so there is no record to read: the manifest
    says which kind it was and that the library kept it, and that is the whole origin.
    """
    return _reject(validator, DeclOrigin(
        kind=kind or "function", name=name, unit_name=library, is_public=False,
    ), loc)


def reject_private_kept(validator: 'TypeValidator', name: str, loc: Any,
                        *, kinds: AbstractSet[str]) -> bool:
    """Reject a MENTION of a name a library declares and keeps, of one of these kinds.

    The call site has its own entry above, because a call knows the name is a callee.
    Every other position -- a type name, a bare constant read -- looks the name up and
    finds nothing, so it has to ask the manifest before it says "unknown".
    """
    kept = validator.library_not_exported.get(name)
    if kept is None:
        return False
    library, kind = kept
    if kind not in kinds:
        return False
    return _reject(validator, DeclOrigin(
        kind=kind, name=name, unit_name=library, is_public=False,
    ), loc)


def reject_private_name(validator: 'TypeValidator', kind: str, record: Any,
                        loc: Any, *, verb: Optional[str] = None) -> bool:
    """Reject a bare mention of another unit's private declaration (a constant).

    A constant has no call to hang the rule on: `visit_name` is where a bare name is
    validated, so it is where the fence sits (D3). A function value passes `VALUE_VERB`.
    """
    return _reject(validator, origin_of(kind, record), loc, verb)


def reject_private_type(validator: 'TypeValidator', name: str, loc: Any) -> bool:
    """Reject a use of another unit's private struct or enum. True when refused.

    One namespace holds both kinds, so the name is looked up in both: a consumer naming
    `Mood` is refused whether the private declaration next door is a struct or an enum.
    A name with no record -- every monomorphized instance, `Result`, `FileMode`, a lifted
    closure environment -- is public by absence.

    A SYNTHESIZED body names nothing (#725, and #702's ruling on this second seam). A
    monomorphized copy is parked in the unit that declares the template, so the copy's
    body reads as that unit's, but the type argument was written at the CALL SITE and
    that site's own unit validated it. The guard is the one its sibling
    `reject_out_of_scope_type` already carries.
    """
    if getattr(validator, "in_synthesized_body", False):
        return False
    table = getattr(validator, "visibility", None)
    if table is None:
        return False
    for kind in ("struct", "enum"):
        origin = table.origin(kind, name)
        if origin is not None:
            return _reject(validator, origin, loc)
    return False


# --- Extension methods (`docs/design/extension-visibility.md`) -------------------------


def calling_unit(validator: 'TypeValidator') -> Optional[str]:
    """The unit whose code is being validated, for the method rule.

    A copy of a compiled library's template is checked in a unit of the consumer and
    reads the scope of the library unit that declares it (#1120). Its calls are that
    unit's calls, so the scope says which unit asks.
    """
    unit = getattr(validator.scope, "unit", None)
    return unit if unit is not None else validator.current_unit_name


def extension_reaches(validator: 'TypeValidator', records: list, receiver_type: Any
                      ) -> list[tuple[Any, MethodReach]]:
    """What each extension record is to the unit being validated.

    A transplanted library body may call its library's private methods, as it may call
    its private functions (#468).
    """
    asker = calling_unit(validator)
    escape = bool(getattr(validator, "in_library_body", False))
    found = []
    for record in records:
        reach = extension_reach(validator.visibility, record, receiver_type, asker,
                                validator.scope)
        if reach is MethodReach.PRIVATE and escape:
            reach = MethodReach.VISIBLE
        found.append((record, reach))
    return found


def _method_name(receiver_type: Any, method_name: str) -> str:
    from sushi_lang.semantics.generics.type_display import display_type
    return f"{display_type(receiver_type)}.{method_name}"


def _note_declared(validator: 'TypeValidator', diagnostic, record: Any, at: str):
    """A note at a declaration, or a prose note where a record has no span: a compiled
    library ships a signature and no source."""
    if record.name_span is not None and record.filename is not None:
        return diagnostic.note_at(at, record.name_span, record.filename)
    tables = _unit_tables(validator) or {}
    library = getattr(tables.get(record.unit_name), "library", None)
    if library is not None:
        return diagnostic.note(f"library '{library}' declares it")
    return diagnostic.note(f"unit '{record.unit_name}' declares it")


def reject_unreachable_extension(validator: 'TypeValidator', receiver_type: Any,
                                 method_name: str,
                                 reaches: list[tuple[Any, MethodReach]], loc: Any) -> None:
    """Refuse a call of extension methods that this unit may not call.

    A public method of a unit that this unit does not import is CE3022, and its help
    names the import: that is the fault the user can correct here. Otherwise the method
    is private to another unit, and that is CE3005.
    """
    name = _method_name(receiver_type, method_name)
    for record, reach in reaches:
        if reach is MethodReach.NOT_IMPORTED:
            written = import_target(record.unit_name, tables=_unit_tables(validator))
            diagnostic = er.emit_with(validator.reporter, er.ERR.CE3022, loc,
                                      name=name, owner=record.unit_name)
            _note_declared(validator, diagnostic, record, "declared here, with `public`") \
                .help(f"add `use {written}` above the first declaration of this unit") \
                .emit()
            return
    for record, reach in reaches:
        if reach is MethodReach.PRIVATE:
            reject_private_cross_unit_use(
                validator.reporter,
                DeclOrigin(kind=EXTENSION_METHOD, name=name, unit_name=record.unit_name,
                           filename=record.filename, name_span=record.name_span,
                           is_public=False),
                loc, current_unit=calling_unit(validator))
            return


def reject_ambiguous_extension(validator: 'TypeValidator', receiver_type: Any,
                               method_name: str, records: list, loc: Any) -> None:
    """CE3023 at a call that two or more imported public extensions answer (C4)."""
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE3023, loc,
                              name=_method_name(receiver_type, method_name))
    for record in records:
        diagnostic = _note_declared(validator, diagnostic, record,
                                    f"unit '{record.unit_name}' declares it here")
    diagnostic.help("a method call cannot name the unit it means: move the code that "
                    "needs each method into a unit of its own, or rename one of the "
                    "methods").emit()
