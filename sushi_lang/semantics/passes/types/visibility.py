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
from sushi_lang.semantics.namespaces import import_help
from sushi_lang.semantics.visibility import (
    DeclOrigin,
    origin_of,
    reject_private_cross_unit_use,
)

if TYPE_CHECKING:
    from . import TypeValidator

__all__ = ["name_is_contested", "out_of_scope_help", "reject_ambiguous_name",
           "reject_private_call", "reject_private_kept",
           "reject_private_kept_call", "reject_private_name",
           "reject_private_type"]


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
                return import_help(origin.unit_name)
    if kind != "function":
        return None
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


def reject_ambiguous_name(validator: 'TypeValidator', kind: str, name: str,
                          loc: Any) -> bool:
    """CE3012: more than one unit in scope offers this name. True when it was refused.

    Section 6 of `docs/design/unit-namespaces.md`. The refusal stands at the USE, where
    the choice was not made, and it names every candidate. CE3003 refused the whole
    program instead, for a collision that might never be written.
    """
    table = getattr(validator, "visibility", None)
    if table is None:
        return False
    candidates = table.candidates(kind, name, validator.current_unit_name,
                                  validator.scope)
    if len(candidates) < 2:
        return False

    diagnostic = er.emit_with(validator.reporter, er.ERR.CE3012, loc, name=name)
    for origin in candidates:
        if origin.name_span is not None:
            diagnostic = diagnostic.note(
                f"unit '{origin.unit_name}' declares it here",
                origin.name_span, origin.filename)
    first = candidates[0].unit_name
    diagnostic.help(f"say which one: `use \"{first}\" as u` above, then `u.{name}`") \
        .emit()
    return True


def _reject(validator: 'TypeValidator', origin: DeclOrigin, loc: Any) -> bool:
    return reject_private_cross_unit_use(
        validator.reporter, origin, loc,
        current_unit=validator.current_unit_name,
        table=getattr(validator, "visibility", None),
        in_library_body=bool(getattr(validator, "in_library_body", False)),
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
                        loc: Any) -> bool:
    """Reject a bare mention of another unit's private declaration (a constant).

    A constant has no call to hang the rule on: `visit_name` is where a bare name is
    validated, so it is where the fence sits (D3).
    """
    return _reject(validator, origin_of(kind, record), loc)


def reject_private_type(validator: 'TypeValidator', name: str, loc: Any) -> bool:
    """Reject a use of another unit's private struct or enum. True when refused.

    One namespace holds both kinds, so the name is looked up in both: a consumer naming
    `Mood` is refused whether the private declaration next door is a struct or an enum.
    A name with no record -- every monomorphized instance, `Result`, `FileMode`, a lifted
    closure environment -- is public by absence.
    """
    table = getattr(validator, "visibility", None)
    if table is None:
        return False
    for kind in ("struct", "enum"):
        origin = table.origin(kind, name)
        if origin is not None:
            return _reject(validator, origin, loc)
    return False
