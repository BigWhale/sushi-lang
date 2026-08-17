"""The peek / poke borrow counters and the `peek x` / `poke x` expression."""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors.registry import ErrorMessage
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Borrow, Expr, MemberAccess, Name
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.typesys import ReferenceType

from .diagnostics import emit_use_after_move, expr_to_string
from .reads import member_access_base
from .state import BorrowState
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from . import BorrowChecker


def check_borrow(checker: 'BorrowChecker', borrow: Borrow) -> None:
    """Check a borrow expression: `peek x`, `poke x`, `peek x.field`, `poke x.field`."""
    is_poke = borrow.mutability == "poke"
    target = borrow.expr
    # The CW2409 re-borrow warning is for the WHOLE variable only: `poke r.field` where
    # `r` is already a `poke` reference borrows the field, not the reference again.
    warn_reborrow = isinstance(target, Name)
    if isinstance(target, MemberAccess):
        target = member_access_base(target)

    if not isinstance(target, Name):
        # A call result, a literal, a member chain off one -- nothing with an address.
        checker.err.emit(er.ERR.CE2404, borrow.loc, expr=expr_to_string(borrow.expr))
        return

    name = target.id
    # An unfindable name was ALREADY reported by the scope pass, which owns names
    # (CE1001 or CE2400). Asking again here gave one token two diagnostics, and the wrong
    # one, because `borrow_state` cannot tell the two cases apart.
    state = checker.borrow_state.get(name)
    if state is None:
        return

    if state.is_moved:
        emit_use_after_move(checker, name, borrow.loc, state)
        return

    if is_poke:
        # A `poke` of a read-only receiver hands the write to a callee, which upgrades
        # the borrow (CE2408 / CE2414 / CE2421). A `peek` stays legal.
        if reject_readonly_write(checker, name, borrow.loc, "take a `poke` borrow"):
            return
        # A `poke` may mutate or free, so it conflicts with a live `let`-borrow like a
        # mutating method (#242). CE2412 not CE2407: the user wrote no `peek`.
        check_owner_not_borrowed(checker, name, borrow.loc, "take `poke`")

    acquire_borrow(checker, state, borrow.loc,
                   is_poke=is_poke, warn_reborrow=warn_reborrow)


def acquire_borrow(checker: 'BorrowChecker', state: BorrowState, span: Optional[Span],
                   *, is_poke: bool, warn_reborrow: bool = False) -> bool:
    """Take a borrow of `state`, or report the conflict that blocks it.

    THE place the counters move and the only emit site for CE2403, CE2407 and CW2409, so
    an explicit `poke x` and an unmarked argument's implicit borrow cannot drift apart.
    """
    if is_poke:
        if state.poke_borrow_count > 0:
            return _conflict(checker, er.ERR.CE2403, state, span)
        if state.peek_borrow_count > 0:
            return _conflict(checker, er.ERR.CE2407, state, span)
        if (warn_reborrow and isinstance(state.var_type, ReferenceType)
                and state.var_type.is_poke()):
            checker.err.emit(er.ERR.CW2409, span, name=state.name)
        state.poke_borrow_count = 1
        state.first_borrow_span = span
    else:
        if state.poke_borrow_count > 0:
            return _conflict(checker, er.ERR.CE2407, state, span)
        if state.peek_borrow_count == 0:
            state.first_borrow_span = span
        state.peek_borrow_count += 1

    checker.active_borrows.add(state.name)
    return True


def _conflict(checker: 'BorrowChecker', code: ErrorMessage, state: BorrowState,
              span: Optional[Span]) -> bool:
    """Report a borrow the live borrows forbid, pointing at the one already held."""
    checker.err.emit_with(code, span, name=state.name) \
        .note("first borrowed here", state.first_borrow_span).emit()
    return False


def register_implicit_borrow(checker: 'BorrowChecker', arg: Expr) -> None:
    """Count an unmarked argument as the shared borrow it now is."""
    if not isinstance(arg, Name):
        return
    state = checker.borrow_state.get(arg.id)
    if state is None or state.is_moved:
        return
    if checker.types.type_class_of_source(state, state.var_type) is not TypeClass.MOVE:
        return
    acquire_borrow(checker, state, arg.loc, is_poke=False)


def clear_borrows(checker: 'BorrowChecker') -> None:
    """Clear all active borrows (called after expression evaluation)."""
    for var_name in checker.active_borrows:
        state = checker.borrow_state.get(var_name)
        if state is not None:
            state.poke_borrow_count = 0
            state.peek_borrow_count = 0
    checker.active_borrows.clear()
