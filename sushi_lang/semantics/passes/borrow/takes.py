"""The marked field take: `nom s.field` hands one field out of a value we own."""

from __future__ import annotations
from typing import Optional, Tuple, TYPE_CHECKING

from sushi_lang.semantics.ast import Expr, MemberAccess, Name
from sushi_lang.semantics.ownership import Provenance, TypeClass

from .reads import read_type, unwrap_try

if TYPE_CHECKING:
    from . import BorrowChecker


def field_take(checker: 'BorrowChecker', expr: Optional[Expr]
               ) -> Optional[Tuple[str, str]]:
    """The (local, field) a marked take names, or None when `expr` is not one.

    Four conditions, and each is what keeps the take sound. The MARKER is written, so
    an unmarked field read stays the borrow it has always been. The access is ONE step
    off a bare NAME, so there is a local to spend and an intermediate field cannot be
    read through. The name is a local this function OWNS, so no second owner appears
    behind the caller's back -- a borrow falls through to CE2411 like any other read
    through a live owner. And the field OWNS something: a take of a field that owns
    nothing has nothing to hand over, so the marker is an ordinary copy there.
    """
    expr = unwrap_try(expr)
    if not isinstance(expr, MemberAccess) or not getattr(expr, "nom_marked", False):
        return None
    receiver = expr.receiver
    if not isinstance(receiver, Name):
        return None
    from .consume import name_provenance
    if checker.borrow_state.get(receiver.id) is None:
        return None
    if name_provenance(checker, receiver.id) is not Provenance.OWNED:
        return None
    if checker.types.type_class(read_type(checker, expr)) is not TypeClass.MOVE:
        return None
    return receiver.id, expr.member


def spend(checker: 'BorrowChecker', root: str, use_span) -> None:
    """A take spends the WHOLE receiver, which is CE2433's rule read on a struct.

    What suppresses the receiver's own free is the whole value and not one field, so a
    take leaves every other owning field with no owner at all. Spending the receiver is
    what gives them one: the backend destroys them at the take. The spend goes through
    `consume_named`, so the take answers CE2401 for a live borrow and arms a drop flag
    inside a branch exactly as `f(nom s)` does.
    """
    from .consume import consume_named
    consume_named(checker, root, Provenance.OWNED, use_span)
