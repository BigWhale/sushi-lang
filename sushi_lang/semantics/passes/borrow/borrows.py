"""The peek / poke borrow counters and the `peek x` / `poke x` expression."""

from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Borrow, Expr, MemberAccess, Name
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.typesys import ReferenceType

from .diagnostics import emit_use_after_move, expr_to_string
from .reads import member_access_base
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from . import BorrowChecker


def check_borrow(checker: 'BorrowChecker', borrow: Borrow) -> None:
    """Check borrow expression: peek expr or poke expr"""
    is_poke = borrow.mutability == "poke"

    if isinstance(borrow.expr, Name):
        var_name = borrow.expr.id

        # An unfindable name was ALREADY reported by the scope pass, which owns names
        # (CE1001 or CE2400). Asking again here gave one token two diagnostics, and the
        # wrong one, because `borrow_state` cannot tell the two cases apart.
        if var_name not in checker.borrow_state:
            return

        state = checker.borrow_state[var_name]

        if state.is_moved:
            emit_use_after_move(checker, var_name, borrow.loc, state)
            return

        # A `poke` of a read-only receiver hands the write to a callee, which upgrades
        # the borrow (CE2408 / CE2414 / CE2421). A `peek` stays legal.
        if is_poke and reject_readonly_write(
                checker, var_name, borrow.loc, "take a `poke` borrow"):
            return

        # A `poke` may mutate or free, so it conflicts with a live `let`-borrow like a
        # mutating method (#242). CE2412 not CE2407: the user wrote no `peek`.
        if is_poke:
            check_owner_not_borrowed(checker, var_name, borrow.loc, "take `poke`")

        if is_poke:
            if state.poke_borrow_count > 0:
                checker.err.emit_with(er.ERR.CE2403, borrow.loc, name=var_name) \
                    .note("first borrowed here", state.first_borrow_span).emit()
                return
            if state.peek_borrow_count > 0:
                checker.err.emit_with(er.ERR.CE2407, borrow.loc, name=var_name) \
                    .note("first borrowed here", state.first_borrow_span).emit()
                return
            if isinstance(state.var_type, ReferenceType) and state.var_type.is_poke():
                checker.err.emit(er.ERR.CW2409, borrow.loc, name=var_name)
            state.poke_borrow_count = 1
            state.first_borrow_span = borrow.loc
        else:
            if state.poke_borrow_count > 0:
                checker.err.emit_with(er.ERR.CE2407, borrow.loc, name=var_name) \
                    .note("first borrowed here", state.first_borrow_span).emit()
                return
            if state.peek_borrow_count == 0:
                state.first_borrow_span = borrow.loc
            state.peek_borrow_count += 1

        checker.active_borrows.add(var_name)

    elif isinstance(borrow.expr, MemberAccess):
        base = member_access_base(borrow.expr)

        if not isinstance(base, Name):
            expr_str = expr_to_string(borrow.expr)
            checker.err.emit(er.ERR.CE2404, borrow.loc, expr=expr_str)
            return

        base_var = base.id
        if base_var not in checker.borrow_state:
            return

        state = checker.borrow_state[base_var]
        if state.is_moved:
            emit_use_after_move(checker, base_var, borrow.loc, state)
            return

        # The same rule as the Name arm, through a field: `poke peek_ref.field`,
        # `poke binding.field` and `poke self.field` all hand a callee a write that
        # cannot reach the value the user means.
        if is_poke and reject_readonly_write(
                checker, base_var, borrow.loc, "take a `poke` borrow"):
            return

        if is_poke:
            check_owner_not_borrowed(checker, base_var, borrow.loc, "take `poke`")

        if is_poke:
            if state.poke_borrow_count > 0:
                checker.err.emit_with(er.ERR.CE2403, borrow.loc, name=base_var) \
                    .note("first borrowed here", state.first_borrow_span).emit()
                return
            if state.peek_borrow_count > 0:
                checker.err.emit_with(er.ERR.CE2407, borrow.loc, name=base_var) \
                    .note("first borrowed here", state.first_borrow_span).emit()
                return
            state.poke_borrow_count = 1
            state.first_borrow_span = borrow.loc
        else:
            if state.poke_borrow_count > 0:
                checker.err.emit_with(er.ERR.CE2407, borrow.loc, name=base_var) \
                    .note("first borrowed here", state.first_borrow_span).emit()
                return
            if state.peek_borrow_count == 0:
                state.first_borrow_span = borrow.loc
            state.peek_borrow_count += 1

        checker.active_borrows.add(base_var)

    else:
        # Other expressions (function calls, literals, etc.) cannot be borrowed
        expr_str = expr_to_string(borrow.expr)
        checker.err.emit(er.ERR.CE2404, borrow.loc, expr=expr_str)


def register_implicit_borrow(checker: 'BorrowChecker', arg: Expr) -> None:
    """Count an unmarked argument as the shared borrow it now is."""
    if not isinstance(arg, Name):
        return
    state = checker.borrow_state.get(arg.id)
    if state is None or state.is_moved:
        return
    if checker.types.type_class_of_source(state, state.var_type) is not TypeClass.MOVE:
        return
    if state.poke_borrow_count > 0:
        checker.err.emit_with(er.ERR.CE2407, arg.loc, name=arg.id) \
            .note("first borrowed here", state.first_borrow_span).emit()
        return
    if state.peek_borrow_count == 0:
        state.first_borrow_span = arg.loc
    state.peek_borrow_count += 1
    checker.active_borrows.add(arg.id)


def clear_borrows(checker: 'BorrowChecker') -> None:
    """Clear all active borrows (called after expression evaluation)."""
    for var_name in checker.active_borrows:
        if var_name in checker.borrow_state:
            state = checker.borrow_state[var_name]
            state.poke_borrow_count = 0
            state.peek_borrow_count = 0
    checker.active_borrows.clear()
