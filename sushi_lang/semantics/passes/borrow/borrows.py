"""The peek / poke borrow counters, and the borrow of a place for one statement.

A `peek x` or a `poke x` argument (an `InGuaranteed` or an `Inout` argument,
docs/design/ir.md 8.3) and a `Borrow` node borrow their place for the statement
(`check_borrow`). An unmarked argument of an owning local is the shared borrow it now is
(`register_implicit_borrow`). The counters clear at the end of each statement
(`clear_borrows`).
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors.registry import ErrorMessage
from sushi_lang.internals.report import Span
from sushi_lang.semantics.constant_borrow import reject_borrow_of_constant
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.passes.collect import ConstSig
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import BorrowMode
from . import arms
from .diagnostics import emit_use_after_move, spelled
from .state import BorrowState
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from . import BorrowChecker

#: The mode of the borrow that a value passed by address takes (docs/design/ir.md 8.3).
BY_ADDRESS = {Convention.IN_GUARANTEED: BorrowMode.PEEK, Convention.INOUT: BorrowMode.POKE}


def check_borrow(checker: 'BorrowChecker', place: e.ShirExpr, mode: BorrowMode,
                 span: Optional[Span]) -> None:
    """Borrow `place` for the statement: `peek x`, `poke x`.

    A place is a local or a constant here. A field place is not checked yet: it comes with
    the field group, and it stops the walk (`borrow, Field`). A constant is read-only
    storage that the program keeps, so it has an address and no borrow state: a `peek` of it
    reads it, and a `poke` of it is CE2400 at the place, bare or behind an alias. A stdlib
    constant has no record and no storage, so both modes are CE2400
    (`reject_borrow_of_constant`). A call result, a literal and a construction are
    temporaries and have no address: CE2404.
    """
    is_poke = mode is BorrowMode.POKE
    if isinstance(place, e.ConstRef):
        name = checker.decl_name(place.const_id).rpartition(".")[2]
        record = checker.constant_record(place)
        sig = record if isinstance(record, ConstSig) else None
        reject_borrow_of_constant(checker.err, name, sig, place.loc, mode=mode.value,
                                  no_frame_slot=sig is None)
        return
    if isinstance(place, e.Field):
        arms.stop_at(checker, place)
    if not isinstance(place, e.LocalRef):
        checker.err.emit(er.ERR.CE2404, span, expr=spelled(checker, place))
        return

    state = checker.borrow_state.get(place.local)
    if state is None:
        return

    if state.is_moved:
        emit_use_after_move(checker, state.name, span, state)
        return

    if is_poke:
        # A `poke` of a read-only receiver hands the write to a callee, which upgrades
        # the borrow (CE2408 / CE2414 / CE2421). A `peek` stays legal.
        if reject_readonly_write(checker, place.local, span, "take a `poke` borrow"):
            return
        # A `poke` may mutate or free, so it conflicts with a live `let`-borrow like a
        # mutating method (#242). CE2412 not CE2407: the user wrote no `peek`.
        check_owner_not_borrowed(checker, place.local, span, "take `poke`", place=place)

    acquire_borrow(checker, state, span, is_poke=is_poke)


def acquire_borrow(checker: 'BorrowChecker', state: BorrowState, span: Optional[Span],
                   *, is_poke: bool) -> bool:
    """Take a borrow of `state`, or report the conflict that blocks it.

    THE place the counters move and the only emit site for CE2403 and CE2407, so an
    explicit `poke x` and an unmarked argument's implicit borrow cannot drift apart.
    Forwarding a whole `poke` parameter (`inner(poke cur)`) is silent, like forwarding a
    field of one: the borrow ends with the statement, and a real conflict is an error.
    """
    if is_poke:
        if state.poke_borrow_count > 0:
            return _conflict(checker, er.ERR.CE2403, state, span)
        if state.peek_borrow_count > 0:
            return _conflict(checker, er.ERR.CE2407, state, span)
        state.poke_borrow_count = 1
        state.first_borrow_span = span
    else:
        if state.poke_borrow_count > 0:
            return _conflict(checker, er.ERR.CE2407, state, span)
        if state.peek_borrow_count == 0:
            state.first_borrow_span = span
        state.peek_borrow_count += 1

    if state.local is not None:
        checker.active_borrows.add(state.local)
    return True


def _conflict(checker: 'BorrowChecker', code: ErrorMessage, state: BorrowState,
              span: Optional[Span]) -> bool:
    """Report a borrow the live borrows forbid, pointing at the one already held."""
    diag = checker.err.emit_with(code, span, name=state.name)
    if state.first_borrow_span is not None:
        diag.note_at("first borrowed here", state.first_borrow_span)
    diag.emit()
    return False


def register_implicit_borrow(checker: 'BorrowChecker', arg: e.ShirExpr) -> None:
    """Count an unmarked argument of an owning local as the shared borrow it now is.

    A field or an element read whose value owns a resource is a shared borrow of its root
    owner by the same rule; the field group adds it.
    """
    if not isinstance(arg, e.LocalRef):
        return
    state = checker.borrow_state.get(arg.local)
    if state is None or state.is_moved:
        return
    if checker.types.type_class_of_source(state, state.var_type) is not TypeClass.MOVE:
        return
    acquire_borrow(checker, state, arg.loc, is_poke=False)


def clear_borrows(checker: 'BorrowChecker') -> None:
    """Clear all active borrows (called at the end of each statement)."""
    for local in checker.active_borrows:
        state = checker.borrow_state.get(local)
        if state is not None:
            state.poke_borrow_count = 0
            state.peek_borrow_count = 0
    checker.active_borrows.clear()
