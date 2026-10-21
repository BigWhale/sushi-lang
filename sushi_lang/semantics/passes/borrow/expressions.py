"""The expression arms of the borrow pass over SHIR (docs/design/ir.md 7.4, 7.9).

A read of a local reports a use after a move (CE2405, CE2435), after a destroy (CE2406)
and after a change of the owner of a `let`-borrow (CE2412). A literal and a constant read
nothing that the body owns. An operator reads its operands, and an interpolated string
reads its holes. Each payload of an enum construction is a consuming use, and so is the
operand of a `??`: the pass decides its `Ownership` and writes the row (`consume.py`). A
`Borrow` borrows its place for the statement.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import exprs as e

from . import arms
from .borrows import check_borrow as borrow_place
from .consume import consume, unwrap_place
from .diagnostics import emit_use_after_move, emit_use_of_invalidated_borrow
from .flow import record_exit

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker


def check_leaf(_checker: 'BorrowChecker', _node: e.Lit | e.ConstRef) -> None:
    """A literal or a constant: it reads nothing that the body owns."""


def check_local_ref(checker: 'BorrowChecker', node: e.LocalRef) -> None:
    read_local(checker, node.local, node.loc)


def read_local(checker: 'BorrowChecker', local: LocalId, span: Optional[Span]) -> None:
    """A read of a local: a use after a move, after a destroy, or after an invalidation."""
    state = checker.borrow_state.get(local)
    if state is None:
        return
    if state.is_moved:
        emit_use_after_move(checker, state.name, span, state)
    elif state.is_destroyed:
        checker.err.emit(er.ERR.CE2406, span, name=state.name)
    else:
        reject_a_use_after_the_change(checker, local, span)


def reject_a_use_after_the_change(checker: 'BorrowChecker', local: LocalId,
                                  span: Optional[Span]) -> bool:
    """Report CE2412 for a use of a binding whose owner changed; True when reported.

    A read and a store through a `poke` reference are both uses (#242, #1026).
    """
    state = checker.borrow_state.get(local)
    if state is None or state.invalidated_at is None:
        return False
    emit_use_of_invalidated_borrow(checker, state.name, span, state)
    return True


def check_unary(checker: 'BorrowChecker', node: e.Unary) -> None:
    arms.check(checker, node.operand)


def check_binary(checker: 'BorrowChecker', node: e.Binary) -> None:
    arms.check(checker, node.lhs)
    arms.check(checker, node.rhs)


def check_interp(checker: 'BorrowChecker', node: e.Interp) -> None:
    """Each hole of an interpolated string is read."""
    for part in node.parts:
        if not isinstance(part, str):
            arms.check(checker, part)


def check_enum_init(checker: 'BorrowChecker', node: e.EnumInit) -> None:
    """A construction consumes each payload by POSITION, marked or not (#134)."""
    for arg in node.payload:
        arms.check(checker, arg.value)
        consume(checker, arg.value)


def check_try(checker: 'BorrowChecker', node: e.Try) -> None:
    """`r??` is a consuming position of its own: an owned wrapper is spent here (#548), and
    a use of `r` after it is CE2405. Its error path returns, so it is an exit of each block
    (`flow.record_exit`)."""
    arms.check(checker, node.inner)
    unwrap_place(checker, node)
    record_exit(checker)


def check_borrow(checker: 'BorrowChecker', node: e.Borrow) -> None:
    """`peek x` or `poke x` in a position that is no call argument: a hole of an
    interpolated string reads the value through the borrow."""
    borrow_place(checker, node.place, node.mode, node.loc)
