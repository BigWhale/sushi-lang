"""The borrow half of the borrowed return `peek T` (#1229, docs/design/borrow-model.md S10h).

Two sides of one rule. In the BODY of a `peek` function, each returned value must be a
borrow whose owner is a source of the function (CE2443). At a CALL, the answer is a
let-borrow of the owners of every argument in the source set (`callee_borrows`, which the
typecheck pass stamps): each owner is frozen while the answer lives, and a temporary
source argument is legal in one statement and refused in a `let` (CE2444).

A lent iterator that leaves through a `peek` return keeps its state on the heap: each link
of its chain is stamped `iterator_leaves`, the `let`s it was bound through included.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    Borrow, Call, DotCall, Expr, MethodCall, Name, StringLit, TryExpr)
from sushi_lang.semantics.borrowed_return import (
    RECEIVER_POSITION, BorrowedReturn, is_borrowed_call, sources_in)
from sushi_lang.semantics.generics.iterators import LENT
from sushi_lang.semantics.ownership import Provenance
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import IteratorType

from .diagnostics import expr_to_string
from .reads import borrow_owner, names_kept_storage

if TYPE_CHECKING:
    from . import BorrowChecker


@dataclass(frozen=True)
class BodyReturn:
    """The borrowed return of the body being checked: its record, sources and parameters."""
    record: BorrowedReturn
    sources: tuple[str, ...]
    params: frozenset[str]


def body_return(checker: 'BorrowChecker', record: Optional[BorrowedReturn],
                params) -> Optional[BodyReturn]:
    """The borrowed return of a body, with its source set judged over the tables."""
    if record is None:
        return None
    return BodyReturn(record, sources_in(record, checker.tables),
                      frozenset(param.name for param in params))


# ---------------------------------------------------------------------------------------
# The call side


def _strip(expr: Expr) -> Expr:
    """The argument under a `peek` / `poke` marker, and the call under a `??`."""
    while isinstance(expr, (Borrow, TryExpr)):
        expr = expr.expr
    return expr


def call_sources(call: Expr) -> tuple[Expr, ...]:
    """The arguments of a borrowed call that stand in the source set of its callee."""
    call = _strip(call)
    out: list[Expr] = []
    for position in getattr(call, "callee_borrows", None) or ():
        if position == RECEIVER_POSITION:
            receiver = getattr(call, "receiver", None)
            if receiver is not None:
                out.append(receiver)
        elif position < len(call.args):
            out.append(call.args[position])
    return tuple(out)


def owners_of_place(checker: 'BorrowChecker', owner: str) -> tuple[str, ...]:
    """The owners a view of the place rooted at `owner` freezes.

    A view through a `let`-borrow reads the storage of its owners too, and a lent
    iterator borrows its owners and keeps nothing to read.
    """
    state = checker.borrow_state.get(owner)
    if state is not None and state.is_lent_iterator:
        return state.borrows_from
    if state is not None and state.is_let_borrow:
        return (owner, *state.borrows_from)
    return (owner,)


def source_owners(checker: 'BorrowChecker',
                  source: Expr) -> tuple[tuple[str, ...], Optional[Expr]]:
    """The owners one source argument lends, and the temporary it is, if it is one."""
    from .iterators import lent_owners
    while isinstance(source, Borrow):
        source = source.expr
    if is_borrowed_call(source):
        return call_owners(checker, source)
    lent = lent_owners(checker, source)
    if lent is not None:
        return lent, None
    if isinstance(source, StringLit):
        # A literal is storage the program keeps: it has no owner, and it is never freed.
        return (), None
    if names_kept_storage(checker, source):
        owner = borrow_owner(source)
        return (owners_of_place(checker, owner) if owner is not None else ()), None
    return (), source


def call_owners(checker: 'BorrowChecker',
                call: Expr) -> tuple[tuple[str, ...], Optional[Expr]]:
    """The owners the answer of a borrowed call borrows, and its first temporary source."""
    owners: tuple[str, ...] = ()
    temporary: Optional[Expr] = None
    for source in call_sources(call):
        found, temp = source_owners(checker, source)
        owners += tuple(owner for owner in found if owner not in owners)
        temporary = temporary or temp
    return owners, temporary


def settle_borrowed_call(checker: 'BorrowChecker', expr: Call | MethodCall | DotCall) -> None:
    """A borrowed call that answers an iterator makes a LENT iterator: it owns its state
    and borrows the owners of its sources, as a producer over a place does. A `Result`
    whose `Ok` holds the iterator is one too, and so is the payload that `??` gives."""
    if not is_borrowed_call(expr):
        return
    answer = checker.types.resolve_named(expr.inferred_return_type)
    ok = checker.types.variant_payload_types(answer, "Ok") if is_instance_of(
        answer, "Result") else ()
    if isinstance(answer, IteratorType) or any(
            isinstance(checker.types.resolve_named(arm), IteratorType) for arm in ok):
        expr.iterator_state = LENT


def reject_view_of_temporary(checker: 'BorrowChecker', expr: Expr) -> bool:
    """CE2444 for a `let` of the answer of a borrowed call over a temporary. True when refused."""
    if not is_borrowed_call(expr):
        return False
    _owners, temporary = call_owners(checker, expr)
    if temporary is None:
        return False
    call = _strip(expr)
    checker.err.emit_with(er.ERR.CE2444, expr.loc, callee=_callee_text(call)) \
        .note_at("this argument is a temporary", temporary.loc) \
        .help("bind the argument first and give the binding, or copy the answer with "
              "`.clone()`").emit()
    return True


def _callee_text(call: Expr) -> str:
    if isinstance(call, Call):
        return expr_to_string(call.callee)
    return getattr(call, "method", "the callee")


# ---------------------------------------------------------------------------------------
# The body side


def check_borrowed_return(checker: 'BorrowChecker', value: Expr) -> None:
    """A `return` in a `peek` body: the value, or the payload of `Result.Ok(...)`, must be a
    borrow whose owner is a source (CE2443). `Result.Err(...)` is an ordinary return."""
    from .consume import consume
    from .expressions import check_expr

    spelled = _result_variant(value)
    if spelled == "Err":
        check_expr(checker, value)
        consume(checker, value)
        return
    if spelled == "Ok":
        value.ownership_provenance = Provenance.FRESH
        if not value.args:
            return
        value = value.args[0]
    check_expr(checker, value)
    returned = checker.borrowed_return
    if returned is None or not returned.sources:
        # A refused position or an empty source set was reported at the declaration.
        _hand_over(checker, value)
        return
    reason = _why_not_a_view(checker, returned, value)
    if reason is not None:
        _emit_not_a_view(checker, returned, value, reason)
    _hand_over(checker, value)


def _result_variant(value: Expr) -> Optional[str]:
    """`Ok` or `Err` when `value` is written `Result.Ok(...)` / `Result.Err(...)`."""
    if (isinstance(value, (DotCall, MethodCall)) and isinstance(value.receiver, Name)
            and value.receiver.id == "Result" and value.method in ("Ok", "Err")):
        return value.method
    return None


def _why_not_a_view(checker: 'BorrowChecker', returned: BodyReturn,
                    value: Expr) -> Optional[str]:
    """Why `value` is not a borrow of a source, or None when it is one."""
    from .iterators import lent_owners
    if is_borrowed_call(value):
        owners, temporary = call_owners(checker, value)
        return _TEMPORARY if temporary is not None else _outside(checker, returned, owners)
    lent = lent_owners(checker, value)
    if lent is not None:
        return _outside(checker, returned, lent)
    if names_kept_storage(checker, value):
        owner = borrow_owner(value)
        if owner is None or owner in returned.sources:
            return None
        state = checker.borrow_state.get(owner)
        if state is not None and (state.is_let_borrow or state.is_lent_iterator):
            return _TEMPORARY if not state.borrows_from else _outside(
                checker, returned, state.borrows_from)
        if isinstance(value, Name) and owner not in returned.params:
            return _OWNED
        return _outside(checker, returned, (owner,))
    return _TEMPORARY if _reads_a_temporary(value) else _OWNED


_TEMPORARY = "it borrows a temporary, which is freed at the end of the statement"
_OWNED = "it is an owned value, not a borrow of a source"


def _reads_a_temporary(value: Expr) -> bool:
    """Is `value` a read through a temporary: an index, a slice or a field of one?"""
    from sushi_lang.semantics.ast import IndexAccess, MemberAccess
    return isinstance(value, (IndexAccess, MemberAccess))


def _outside(checker: 'BorrowChecker', returned: BodyReturn,
             owners: tuple[str, ...]) -> Optional[str]:
    """The reason for the first owner that is not a source, or None when all are."""
    for owner in owners:
        if owner in returned.sources or _is_a_view(checker, owner):
            continue
        if owner in returned.params:
            return f"it borrows '{owner}', which is not a source of the answer"
        return f"it borrows the local '{owner}', which the function frees"
    return None if owners else _TEMPORARY


def _is_a_view(checker: 'BorrowChecker', name: str) -> bool:
    """Is `name` a `let` of a view whose owners are known? Its owners are judged instead."""
    state = checker.borrow_state.get(name)
    return (state is not None and state.is_let_borrow and not state.is_lent_iterator
            and bool(state.borrows_from))


def _emit_not_a_view(checker: 'BorrowChecker', returned: BodyReturn, value: Expr,
                     reason: str) -> None:
    sources = ", ".join(f"'{source}'" for source in returned.sources) or "nothing"
    checker.err.emit_with(er.ERR.CE2443, value.loc,
                          value=expr_to_string(value, temporaries=True), reason=reason) \
        .note_at(f"the answer is declared a borrow of {sources} here", returned.record.span) \
        .help(f"return a view of {sources}, or drop `peek` and return an owned value") \
        .emit()


def _hand_over(checker: 'BorrowChecker', value: Expr) -> None:
    """Give the returned view to the caller: nothing here frees it.

    A `let` that holds a lent iterator owns the value, so it is spent; every other view
    owns nothing, and the position adopts it as it is. A lent iterator moves its state to
    the heap.
    """
    from .consume import consume_named
    from .iterators import is_lent_name
    if isinstance(value, Name) and is_lent_name(checker, value):
        value.ownership_provenance = Provenance.OWNED
        consume_named(checker, value.id, Provenance.OWNED, value.loc)
    else:
        value.ownership_provenance = Provenance.FRESH
    lend_to_the_caller(checker, value)


def lend_to_the_caller(checker: 'BorrowChecker', value: Optional[Expr]) -> None:
    """Stamp each link of a lent iterator chain that leaves the function: its state goes
    on the heap. A `let` is followed to the value it was bound to; a clone keeps its own
    heap state, and a borrowed call already gives one."""
    from .iterators import adapter_inputs, is_adapter, is_producer
    if isinstance(value, Name):
        state = checker.borrow_state.get(value.id)
        if state is not None and state.lent_value is not None:
            lend_to_the_caller(checker, state.lent_value)
        return
    if not isinstance(value, (MethodCall, DotCall)) or value.iterator_state != LENT:
        return
    if is_producer(value):
        value.iterator_leaves = True
    elif is_adapter(value):
        value.iterator_leaves = True
        for source in adapter_inputs(value):
            lend_to_the_caller(checker, source)
