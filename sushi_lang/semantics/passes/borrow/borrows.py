"""The peek / poke borrow counters, and the borrow of a place for one statement.

A `peek x` or a `poke x` argument (an `InGuaranteed` or an `Inout` argument,
docs/design/ir.md 8.3) and a `Borrow` node borrow their place for the statement
(`check_borrow`). An unmarked argument of an owning local is the shared borrow it now is
(`register_implicit_borrow`). A borrow of a place in a `var` is counted on the state of
the `var` (`storage_state`). The counters clear at the end of each statement
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
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import BorrowMode
from . import arms
from .diagnostics import emit_use_after_move, temporary_spelled
from .reads import borrow_owner, read_type, storage_state
from .state import BorrowState
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from . import BorrowChecker

#: The mode of the borrow that a value passed by address takes (docs/design/ir.md 8.3).
BY_ADDRESS = {Convention.IN_GUARANTEED: BorrowMode.PEEK, Convention.INOUT: BorrowMode.POKE}

#: The root of a place that a later group checks: a function used as a value.
LATER_ROOTS = (e.GlobalRef,)


def check_borrow(checker: 'BorrowChecker', place: e.ShirExpr, mode: BorrowMode,
                 span: Optional[Span]) -> None:
    """Borrow `place` for the statement: `peek x`, `poke x`, `peek x.f`, `poke x.f.g`.

    A borrow of a field is counted on the ROOT of the place: the pass tracks whole locals,
    not the places in them. A constant is read-only storage that the program keeps, so it
    has an address and no borrow state: a `peek` of it or of its field reads it, and a
    `poke` is CE2400 at the name of the constant, bare or behind an alias. A stdlib
    constant has no record and no storage, so both modes are CE2400
    (`reject_borrow_of_constant`). A `var` is storage with an address: a borrow of it or
    of its field is counted on its state (`storage_state`), and a `poke` of it changes it
    for each `let`-borrow of it. A call result, a literal and a
    construction are temporaries and have no address, and nor does a field of one: CE2404.
    An element has no stable address either, because a growth of its array can move it:
    CE2404. A slice is a view of its array (`_borrow_slice`). A root that a later group
    checks (`LATER_ROOTS`) stops the walk.
    """
    is_poke = mode is BorrowMode.POKE
    if isinstance(place, e.Slice):
        _borrow_slice(checker, place, span, is_poke=is_poke)
        return
    if borrow_of_constant(checker, place, mode):
        return
    root = walk_place(place, Step.MEMBER).node
    if isinstance(root, LATER_ROOTS):
        arms.stop_at(checker, root)
    if isinstance(root, e.StorageRef):
        state = storage_state(checker, root)
        if is_poke:
            check_owner_not_borrowed(checker, state.local, span, "take `poke`", place=place)
        acquire_borrow(checker, state, span, is_poke=is_poke)
        return
    if not isinstance(root, e.LocalRef):
        checker.err.emit(er.ERR.CE2404, span, expr=temporary_spelled(checker, place))
        return

    state = checker.borrow_state.get(root.local)
    if state is None:
        return

    if state.is_moved:
        emit_use_after_move(checker, state.name, span, state)
        return

    if is_poke:
        # A `poke` of a read-only receiver hands the write to a callee, which upgrades
        # the borrow (CE2408 / CE2414 / CE2421). A `peek` stays legal.
        if reject_readonly_write(checker, root.local, span, "take a `poke` borrow"):
            return
        # A `poke` may mutate or free, so it conflicts with a live `let`-borrow like a
        # mutating method (#242). CE2412 not CE2407: the user wrote no `peek`.
        check_owner_not_borrowed(checker, root.local, span, "take `poke`", place=place)

    acquire_borrow(checker, state, span, is_poke=is_poke)


def _borrow_slice(checker: 'BorrowChecker', place: e.Slice, span: Optional[Span], *,
                  is_poke: bool) -> None:
    """`peek data[a..b]`: a pointer to the slice descriptor (docs/design/slices.md).

    A `peek` reads the array: it is a shared borrow of the owner of the array, so a `poke`
    of the same owner in one call is CE2407. A `poke` is refused (S4): a callee could grow
    the view and move storage that the array keeps.
    """
    arms.check(checker, place)
    owner = borrow_owner(checker, place)
    if is_poke:
        reject_readonly_write(checker, owner, span, "take a `poke` borrow", receiver=place)
        return
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None:
        acquire_borrow(checker, state, span, is_poke=False)


def borrow_of_constant(checker: 'BorrowChecker', place: e.ShirExpr, mode: BorrowMode) -> bool:
    """Is the root of `place` a constant? A `peek` of it reads it, and a `poke` is CE2400
    at the name of the constant (`reject_borrow_of_constant`). True when the root is one."""
    root = walk_place(place, Step.MEMBER).node
    if not isinstance(root, e.ConstRef):
        return False
    name = checker.decl_name(root.const_id).rpartition(".")[2]
    record = checker.constant_record(root)
    sig = record if isinstance(record, ConstSig) else None
    reject_borrow_of_constant(checker.err, name, sig, root.loc, mode=mode.value,
                              no_frame_slot=sig is None)
    return True


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

    A field read, an element read and a slice whose value owns a resource are a shared
    borrow of their ROOT owner by the same rule: the callee reads storage that a `poke` of
    the owner can move. A plain field and a plain element are a copy.
    """
    walked = walk_place(arg, Step.MEMBER | Step.INDEX)
    if walked.local is None:
        return
    state = checker.borrow_state.get(walked.local)
    if state is None or state.is_moved:
        return
    owns = (checker.types.type_class(read_type(checker, arg)) if walked.path
            else checker.types.type_class_of_source(state, state.var_type))
    if owns is not TypeClass.MOVE:
        return
    acquire_borrow(checker, state, arg.loc, is_poke=False)


def clear_borrows(checker: 'BorrowChecker') -> None:
    """Clear all active borrows (called at the end of each statement)."""
    for state in [checker.borrow_state.get(owner) for owner in checker.active_borrows]:
        if state is not None:
            state.poke_borrow_count = 0
            state.peek_borrow_count = 0
    checker.active_borrows.clear()
