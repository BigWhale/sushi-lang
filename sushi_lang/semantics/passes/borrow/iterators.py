"""An iterator value in the borrow pass over SHIR (#1259, docs/design/ir.md 4.4, 7.9).

A built-in producer over a place (`xs.iter()`, `m.keys()`) makes a LENT iterator: a new value
that owns its own state and borrows the owners of the place. A producer over a temporary makes
an OWNED iterator, which takes the temporary. The clone of a lent iterator is lent and borrows
the same owners, and so is a lazy adapter with a lent input and the answer of a `peek` return.
Two facts, kept apart: a lent iterator is a `let`-borrow of its owners (frozen to its last use,
CE2411 when it would leave), and it is still destroyed, because it owns its value.

`borrow` is the one writer of the two rows of a call: LENT or OWNED, and the heap decision of a
lent one. A lent state is on the stack, except when the iterator leaves through a `peek` return
(`lend_to_the_caller`) and for a clone and the answer of a `peek` return, which are on the heap
already.

A walk of an `IteratorValue` is in place for a `let` that holds a lent iterator and for a place
under a `poke` reference. Any other value goes to the loop: a lent temporary with its owners
frozen for the loop, any other value by a consuming use.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.semantics.generics.iterators import (
    is_lazy_adapter, produces_from_a_container)
from sushi_lang.semantics.method_effects import ITERATOR_FAMILY
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.shir.ownership import OwnershipResults
from sushi_lang.semantics.typesys import IteratorType, ReferenceType, deref_type

from .borrowed_returns import borrowed_call, call_owners
from .reads import borrow_owner, names_an_owner

if TYPE_CHECKING:
    from . import BorrowChecker
    from .state import Owner


def _builtin_of(checker: 'BorrowChecker', node: object) -> Optional[Builtin]:
    """The built-in callee of `node`, or None."""
    if not isinstance(node, e.Call):
        return None
    resolved = checker.answers().callee_of(node.shir_id)
    callee = resolved.callee if resolved is not None else None
    return callee if isinstance(callee, Builtin) else None


def is_producer(checker: 'BorrowChecker', node: object) -> bool:
    callee = _builtin_of(checker, node)
    return callee is not None and produces_from_a_container(
        callee.method.family, callee.method.name, len(node.args))


def _is_clone(checker: 'BorrowChecker', node: object) -> bool:
    callee = _builtin_of(checker, node)
    return (callee is not None and callee.method.family == ITERATOR_FAMILY
            and callee.method.name == "clone")


def _is_lazy_adapter(checker: 'BorrowChecker', node: object) -> bool:
    callee = _builtin_of(checker, node)
    return callee is not None and is_lazy_adapter(callee.method.family, callee.method.name)


def lent_source(checker: 'BorrowChecker', value: e.ShirExpr) -> e.ShirExpr:
    """The place that a producer reads, under each iterator clone. Any other value is itself."""
    while _is_clone(checker, value) or is_producer(checker, value):
        value = _builtin_of(checker, value).recv
    return value


def adapter_inputs(node: e.Call, callee: Builtin) -> tuple[e.ShirExpr, ...]:
    """The iterators that an adapter takes: its receiver, and the argument of `zip`."""
    if callee.method.name == "zip" and node.args:
        return (callee.recv, node.args[0].value)
    return (callee.recv,)


def lent_owners(checker: 'BorrowChecker', value: object) -> Optional[tuple['Owner', ...]]:
    """The owners that a lent iterator borrows, or None when `value` is no lent iterator."""
    call = borrowed_call(checker, value)
    if call is not None:
        answer = checker.answers().type_of(call.shir_id)
        return call_owners(checker, call)[0] if holds_an_iterator(answer) else None
    if isinstance(value, e.LocalRef):
        state = checker.borrow_state.get(value.local)
        return state.borrows_from if state is not None and state.is_lent_iterator else None
    callee = _builtin_of(checker, value)
    if callee is None:
        return None
    if is_producer(checker, value):
        return (owners_through(checker, borrow_owner(checker, callee.recv))
                if names_an_owner(checker, callee.recv) else None)
    if _is_clone(checker, value):
        return lent_owners(checker, callee.recv)
    if _is_lazy_adapter(checker, value):
        return _adapter_owners(checker, value, callee)
    return None


def holds_an_iterator(ty: object) -> bool:
    """Is `ty` an iterator, or a `Result` whose `Ok` is one (`peek Iterator@(T) | E`)?"""
    from sushi_lang.semantics.generics.iterators import answers_an_iterator
    return answers_an_iterator(ty)


def _adapter_owners(checker: 'BorrowChecker', node: e.Call,
                    callee: Builtin) -> Optional[tuple['Owner', ...]]:
    """The owners of each lent input of a lazy adapter, or None with no lent input."""
    owners: Optional[tuple[Owner, ...]] = None
    for source in adapter_inputs(node, callee):
        found = lent_owners(checker, source)
        if found is not None:
            owners = (*(owners or ()), *(owner for owner in found
                                         if owner not in (owners or ())))
    return owners


def owners_through(checker: 'BorrowChecker', owner: Optional['Owner']) -> tuple['Owner', ...]:
    """The owners that a borrow of `owner` freezes: the owners of a lent iterator, else
    `owner`, and the owners of a view."""
    from .consume import view_owners
    if owner is None:
        return ()
    state = checker.borrow_state.get(owner)
    if state is not None and state.is_lent_iterator:
        return state.borrows_from
    return view_owners(checker, owner)


def is_lent_name(checker: 'BorrowChecker', value: object) -> bool:
    """Is `value` the name of a `let` that holds a lent iterator?"""
    return isinstance(value, e.LocalRef) and lent_owners(checker, value) is not None


def settle_iterator_call(checker: 'BorrowChecker', node: e.Call, callee: Builtin) -> None:
    """The rows of a producer, a clone and a lazy adapter. A producer over a temporary takes
    the temporary. The heap decision of a lent call waits for the end of the body."""
    from .consume import consume
    producer = is_producer(checker, node)
    if not (producer or _is_clone(checker, node) or _is_lazy_adapter(checker, node)):
        return
    owners = lent_owners(checker, node)
    if producer and owners is None:
        consume(checker, callee.recv)
    if not checker.discovery_rounds:
        checker.table().record_lent(node.shir_id, owners is not None)
        if owners is not None:
            checker.lent_calls[node.shir_id] = _is_clone(checker, node)


def settle_borrowed_iterator(checker: 'BorrowChecker', node: e.Call) -> None:
    """A call with a `peek` return that answers an iterator makes a lent iterator on the heap."""
    if (not checker.discovery_rounds and checker.answers().sources_of(node.shir_id) is not None
            and holds_an_iterator(checker.answers().type_of(node.shir_id))):
        checker.table().record_lent(node.shir_id, True)
        checker.lent_calls[node.shir_id] = True


def write_heap_decisions(checker: 'BorrowChecker', ownership: OwnershipResults) -> None:
    """One heap decision for each lent call of the body (ruling S13)."""
    for call, on_heap in checker.lent_calls.items():
        ownership.record_lent_on_heap(call, on_heap or call in checker.leaving_calls)


def lend_to_the_caller(checker: 'BorrowChecker', value: Optional[e.ShirExpr]) -> None:
    """Each link of a lent iterator that leaves through a `peek` return goes on the heap. A
    `let` is followed to the value that it holds, and a clone and a borrowed call are on the
    heap already."""
    if isinstance(value, e.LocalRef):
        state = checker.borrow_state.get(value.local)
        if state is not None and state.lent_value is not None:
            lend_to_the_caller(checker, state.lent_value)
        return
    callee = _builtin_of(checker, value)
    if callee is None or lent_owners(checker, value) is None:
        return
    if is_producer(checker, value):
        checker.leaving_calls.add(value.shir_id)
    elif _is_lazy_adapter(checker, value):
        checker.leaving_calls.add(value.shir_id)
        for source in adapter_inputs(value, callee):
            lend_to_the_caller(checker, source)


def take_lent_iterator(checker: 'BorrowChecker', node: e.Call, callee: object,
                       source: e.ShirExpr) -> bool:
    """A built-in adapter takes a lent iterator with `nom` (#1228). True when it did: the
    answer borrows the same owners, so the borrow does not leave with it. A `let` that holds
    the iterator is spent, and a lent temporary goes to the adapter whole. Any other position
    that takes a lent iterator refuses it (CE2411)."""
    from sushi_lang.semantics.ownership import Provenance, TypeClass
    from .consume import consume_named, decide
    if (not isinstance(callee, Builtin) or callee.method.family != ITERATOR_FAMILY
            or lent_owners(checker, source) is None):
        return False
    if isinstance(source, e.LocalRef):
        consume_named(checker, source, Provenance.OWNED, source.loc)
    else:
        decide(checker, source, Provenance.FRESH, TypeClass.MOVE)
    return True


def reached_through_poke(checker: 'BorrowChecker', iterable: e.ShirExpr) -> bool:
    """Is the iterable a place whose root is a `poke` reference?"""
    root = walk_place(iterable, Step.MEMBER | Step.INDEX).local
    state = checker.borrow_state.get(root) if root is not None else None
    return (state is not None and isinstance(state.var_type, ReferenceType)
            and state.var_type.is_poke())


def owns_its_items(ty: object) -> bool:
    """Does a walk of an iterator of type `ty` own each item? A `peek` part makes it lend."""
    iterator = deref_type(ty)
    return isinstance(iterator, IteratorType) and not iterator.peeks


LENT_HELP = ("this iterator borrows {owners}, so it cannot leave the function or be kept: "
             "walk it here, or clone the container first, so that the iterator owns the "
             "copy it walks")


def lent_iterator_help(checker: 'BorrowChecker', owners: tuple['Owner', ...]) -> str:
    """The help of CE2411 for a lent iterator: its escape is an owned source."""
    named = ", ".join(f"'{checker.name_of(owner)}'" for owner in owners) or "a container"
    return LENT_HELP.format(owners=named)


def walks_in_place(checker: 'BorrowChecker', loop: s.Loop, iterable: e.ShirExpr) -> bool:
    """The walk-in-place answer of `loop`, written once."""
    in_place = is_lent_name(checker, iterable) or reached_through_poke(checker, iterable)
    if not checker.discovery_rounds:
        checker.table().record_in_place(loop.shir_id, in_place)
    return in_place
