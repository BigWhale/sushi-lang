"""Who may name what: the typecheck pass's view of the visibility seam.

The rule itself lives in `semantics/visibility.py`, which answers for every kind of
declaration. This module is the pass's adapter: it holds the two things only a use site
knows -- the validator it is running inside, and that a transplanted library body is
allowed to call its own library's privates (#468). A call and a bare constant read ask
the same two questions, so they ask them here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sushi_lang.semantics.visibility import (
    DeclOrigin,
    origin_of,
    reject_private_cross_unit_use,
)

if TYPE_CHECKING:
    from . import TypeValidator

__all__ = ["name_is_contested", "reject_private_call", "reject_private_kept_call",
           "reject_private_name"]


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


def reject_private_name(validator: 'TypeValidator', kind: str, record: Any,
                        loc: Any) -> bool:
    """Reject a bare mention of another unit's private declaration (a constant).

    A constant has no call to hang the rule on: `visit_name` is where a bare name is
    validated, so it is where the fence sits (D3).
    """
    return _reject(validator, origin_of(kind, record), loc)
