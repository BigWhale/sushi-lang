"""Who may call whom across units: the CE3005 gate."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from .. import TypeValidator


def reject_private_cross_unit_call(
    validator: 'TypeValidator',
    name: str,
    loc: Any,
    *,
    visible: bool,
    unit_name: Optional[str],
) -> bool:
    """Reject a call to a private function of another unit. True when it was rejected.

    `visible` is the callee's own claim on being callable from elsewhere: `public`, and
    nothing else. What a library ships privately for its own bodies to call is answered
    by the call site instead -- a transplanted library body may call it, and the code the
    user wrote may not (#468).
    """
    if visible or unit_name is None or validator.current_unit_name is None:
        return False
    if getattr(validator, "in_library_body", False):
        return False
    if unit_name == validator.current_unit_name:
        return False

    er.emit(validator.reporter, er.ERR.CE3005, loc,
            name=name,
            current_unit=validator.current_unit_name,
            func_unit=unit_name)
    return True
