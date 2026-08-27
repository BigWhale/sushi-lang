"""Who may call whom across units: the call site's view of the visibility seam.

The rule itself lives in `semantics/visibility.py`, which answers for every kind of
declaration. This module is the CALL site's adapter: it holds the two things only a call
knows -- the validator it is running inside, and that a transplanted library body is
allowed to call its own library's privates (#468).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sushi_lang.semantics.visibility import (
    DeclOrigin,
    origin_of,
    reject_private_cross_unit_use,
)

if TYPE_CHECKING:
    from .. import TypeValidator

__all__ = ["reject_private_call", "reject_private_kept_call"]


def _reject(validator: 'TypeValidator', origin: DeclOrigin, loc: Any) -> bool:
    return reject_private_cross_unit_use(
        validator.reporter, origin, loc,
        current_unit=validator.current_unit_name,
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
