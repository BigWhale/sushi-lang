"""An iterator value that borrows its source (#1259, docs/design/borrow-model.md).

A built-in producer over a PLACE (`xs.iter()`, `m.keys()`) makes a LENT iterator: a new
value that owns its own state and borrows the owners of the place. A producer over a
temporary makes an OWNED iterator, which takes the temporary. The clone of a lent iterator
is lent too, and borrows the same owners. Two facts, kept apart: a lent iterator is a
let-borrow of its owners (frozen while it lives, CE2411 when it would leave), and it is
still destroyed at scope exit, because it owns its value.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.semantics.ast import DotCall, Expr, MethodCall, Name
from sushi_lang.semantics.generics.iterators import (
    CONTAINER_ITERATORS, CURSOR, LENT, OWNED)

from sushi_lang.semantics.places import Step, walk_place

from .reads import borrow_owner, names_kept_storage

if TYPE_CHECKING:
    from . import BorrowChecker

# The built-in families whose iterator methods are producers over a container.
_PRODUCER_FAMILIES = frozenset({"array", "list", "hashmap"})


def is_producer(expr: Optional[Expr]) -> bool:
    """Is `expr` a built-in iterator method of a container: `xs.iter()`, `m.keys()`?"""
    return (isinstance(expr, (MethodCall, DotCall)) and not expr.args
            and expr.method in CONTAINER_ITERATORS
            and expr.callee_builtin_family in _PRODUCER_FAMILIES)


def _is_iterator_clone(expr: Optional[Expr]) -> bool:
    """Is `expr` the built-in `.clone()` of an iterator value?"""
    return (isinstance(expr, (MethodCall, DotCall)) and expr.method == "clone"
            and expr.callee_builtin_family == "iterator")


def settle_iterator_source(checker: 'BorrowChecker', expr: MethodCall | DotCall) -> None:
    """Stamp how a producer keeps its state, and take a temporary source.

    The stamp is the backend's one answer: LENT keeps the state on the stack, OWNED takes
    the receiver and keeps the state on the heap. A direct walk (CURSOR) makes no value.
    """
    if expr.iterator_state == CURSOR:
        return
    if is_producer(expr):
        if names_kept_storage(checker, expr.receiver):
            expr.iterator_state = LENT
            return
        expr.iterator_state = OWNED
        from .consume import consume
        consume(checker, expr.receiver)
        return
    if _is_iterator_clone(expr) and lent_owners(checker, expr.receiver) is not None:
        expr.iterator_state = LENT


def lent_owners(checker: 'BorrowChecker', expr: Optional[Expr]) -> Optional[tuple[str, ...]]:
    """The owners a lent iterator borrows, or None when `expr` is no lent iterator.

    A clone borrows what its receiver borrows, so the walk crosses every lent clone and
    asks the source: a `let` that holds a lent iterator, or a producer over a place.
    """
    if isinstance(expr, (MethodCall, DotCall)) and expr.iterator_state != LENT:
        return None
    source = walk_place(expr, Step.CALL, crosses_call=_is_lent_clone).node
    if isinstance(source, Name):
        state = checker.borrow_state.get(source.id)
        if state is not None and state.is_lent_iterator:
            return state.borrows_from
        return None
    if is_producer(source) and source.iterator_state == LENT:
        return owners_through(checker, borrow_owner(source.receiver))
    return None


def _is_lent_clone(call) -> bool:
    return _is_iterator_clone(call) and call.iterator_state == LENT


def owners_through(checker: 'BorrowChecker', owner: Optional[str]) -> tuple[str, ...]:
    """The owners a borrow of `owner` freezes: a lent iterator's own, else `owner`."""
    if owner is None:
        return ()
    state = checker.borrow_state.get(owner)
    if state is not None and state.is_lent_iterator:
        return state.borrows_from
    return (owner,)


def is_lent_name(checker: 'BorrowChecker', expr: Optional[Expr]) -> bool:
    """Is `expr` the name of a `let` that holds a lent iterator?"""
    return isinstance(expr, Name) and lent_owners(checker, expr) is not None


LENT_HELP = ("this iterator borrows {owners}, so it cannot leave the function or be kept: "
             "walk it here, or clone the container first, so that the iterator owns the "
             "copy it walks")


def lent_iterator_help(owners: tuple[str, ...]) -> str:
    """The help of CE2411 for a lent iterator: its escape is an owned source."""
    named = ", ".join(f"'{owner}'" for owner in owners) or "a container"
    return LENT_HELP.format(owners=named)
