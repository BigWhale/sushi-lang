"""The expression arms of the borrow pass over SHIR (docs/design/ir.md 7.4, 7.9).

A read of a local reports a use after a move (CE2405, CE2435), after a destroy (CE2406)
and after a change of the owner of a `let`-borrow (CE2412). A literal, a constant and a
`var` read nothing that the body owns. An operator reads its operands, an interpolated
string reads its holes, a field reads its base, and an element and a slice read their array and their
index or bounds. Each field of a struct construction, each part of a tuple, each payload
of an enum construction and each element of an array construction is a consuming use, and
so is the operand of a `??`: the pass decides its `Ownership` and writes the row
(`consume.py`). An element and a slice are reads through their array, plain or owning: a
position that takes an owning one refuses it (CE2411), and a `let` of one is a `let`-borrow
of the root. A `Borrow` borrows its place for the statement.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e

from . import arms
from .borrows import check_borrow as borrow_place
from .consume import consume, consume_marked, unwrap_place
from .diagnostics import CopyUse, emit_use_after_move, emit_use_of_invalidated_borrow
from .flow import record_exit

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker


def check_leaf(_checker: 'BorrowChecker', _node: e.Lit | e.ConstRef | e.StorageRef) -> None:
    """A literal, a constant or a `var`: it reads nothing that the body owns, and a `var`
    always holds its value."""


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


def check_cast(checker: 'BorrowChecker', node: e.Cast) -> None:
    """A cast reads its operand and takes it by POSITION: a plain one by value (ADOPT), and
    an owning one, the identity cast `a as string`, as any consuming use (a move of a local,
    CE2411 for a field). The escape of CE2411 casts the copy."""
    arms.check(checker, node.operand)
    target = display_type(checker.answers().type_of(node.shir_id))
    consume(checker, node.operand, CopyUse("cast the copy", f" as {target}"))


def check_interp(checker: 'BorrowChecker', node: e.Interp) -> None:
    """Each hole of an interpolated string is read."""
    for part in node.parts:
        if not isinstance(part, str):
            arms.check(checker, part)


def check_field(checker: 'BorrowChecker', node: e.Field) -> None:
    """A field read reads its base, so a read of a field of a moved local is CE2405 at the
    local. The value is a borrow of the root: a position that takes it decides that."""
    arms.check(checker, node.base)


def check_array_init(checker: 'BorrowChecker', node: e.ArrayInit) -> None:
    """An array construction takes each element of a `Single` run by POSITION, in the
    written order. A repeated value is read, then its count (ruling R2), and each slot
    takes its own copy. A range run reads its bounds."""
    for run in node.runs:
        if isinstance(run, e.Single):
            _take_by_position(checker, run.value)
        elif isinstance(run, e.Repeat):
            read_value(checker, run.value)
            arms.check(checker, run.count)
        else:
            read_value(checker, run.range)


def read_value(checker: 'BorrowChecker', value: e.ShirExpr) -> None:
    """Read a value. A range is no value of its own here: its bounds are read."""
    if isinstance(value, e.Range):
        arms.check(checker, value.start)
        arms.check(checker, value.end)
    else:
        arms.check(checker, value)


def check_index(checker: 'BorrowChecker', node: e.Index) -> None:
    """An element read reads its array, then its index. The value is a borrow of the root
    of the array, as a field is: a position that takes it decides that, and a plain
    element is a copy."""
    arms.check(checker, node.base)
    arms.check(checker, node.index)


def check_slice(checker: 'BorrowChecker', node: e.Slice) -> None:
    """A slice reads its array, then its bounds. The value is a view of the storage of the
    array (docs/design/slices.md): a `let` of it is a let-borrow of the root, and a
    position that takes it over is refused (CE2411)."""
    arms.check(checker, node.base)
    arms.check(checker, node.start)
    arms.check(checker, node.end)


def check_struct_init(checker: 'BorrowChecker', node: e.StructInit) -> None:
    """A construction consumes each field by POSITION, in the order that the source writes
    the fields (ruling R1). A `nom` field of a local that the body owns is a take."""
    for position in node.written_order:
        _take_arg(checker, node.fields[position])


def check_tuple_init(checker: 'BorrowChecker', node: e.TupleInit) -> None:
    """A tuple consumes each part by POSITION."""
    for part in node.parts:
        _take_by_position(checker, part)


def check_enum_init(checker: 'BorrowChecker', node: e.EnumInit) -> None:
    """A construction consumes each payload by POSITION, marked or not (#134)."""
    for arg in node.payload:
        _take_arg(checker, arg)


def _take_by_position(checker: 'BorrowChecker', value: e.ShirExpr) -> None:
    arms.check(checker, value)
    consume(checker, value)


def _take_arg(checker: 'BorrowChecker', arg: e.Arg) -> None:
    """A field or a payload that a construction takes by position: a `nom` one is a take."""
    arms.check(checker, arg.value)
    if arg.marker is Convention.OWNED:
        consume_marked(checker, arg.value)
    else:
        consume(checker, arg.value)


def check_take(checker: 'BorrowChecker', node: e.Take) -> None:
    """`nom s.out`: its place is read. The position that takes it decides whether it is a
    take (`consume.py`)."""
    arms.check(checker, node.place)


def check_try(checker: 'BorrowChecker', node: e.Try) -> None:
    """`r??` is a consuming position of its own: an owned wrapper is spent here (#548), and
    a use of `r` after it is CE2405. Its error path returns, so it is an exit of each block
    (`flow.record_exit`). Its operand is the one position of a read-through `or_err`."""
    checker.try_operands.add(node.inner.shir_id)
    arms.check(checker, node.inner)
    unwrap_place(checker, node)
    record_exit(checker)


def check_borrow(checker: 'BorrowChecker', node: e.Borrow) -> None:
    """`peek x` or `poke x` in a position that is no call argument: a hole of an
    interpolated string reads the value through the borrow."""
    borrow_place(checker, node.place, node.mode, node.loc)
