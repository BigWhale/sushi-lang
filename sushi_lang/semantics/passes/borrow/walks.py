"""The direct walks of the borrow pass over SHIR (docs/design/ir.md 7.7, 7.9).

An `IteratorValue` walk reads an iterator value on each round: it has its own arm. A lent
iterator freezes its owners for the loop in the same way, and its items are read-only views.
Any other item is owned by the round.

A `Cursor` or a `HashMapCursor` makes no value. The walk reads the storage of its container
on each round, so it is a `let`-borrow of the container with a key that no source can spell:
a change that can move or free that storage and that reaches the next round is CE2412 at
the change (#956). A walk of a temporary reads what only the loop keeps, and it freezes
nothing (#1014).

A bare binder is a read-only view of its element (CE2414): an owning item freezes the
owner of the container to its last use, and it has a borrow region. A `peek` or a `poke`
binder is a reference into the element, with the gates of a reference. Each round makes a new
item, so the facts of the item do not go round the back edge.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, cast

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ownership import Provenance
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.typesys import BorrowMode, ReferenceType

from . import arms
from .bindings import declare_bare_binders, release_binding_borrow
from .borrows import clear_borrows
from .consume import consume, decide, source_class, value_owners
from .diagnostics import emit_change_under_iterator, spelled
from .iterators import lent_owners, owns_its_items, walks_in_place
from .reads import borrow_owner, names_an_owner
from .references import freeze_reference
from .regions import note_binder
from .state import BorrowState, record_owners
from .writes import reject_readonly_write, reject_write_through_slice

if TYPE_CHECKING:
    from . import BorrowChecker
    from .state import Owner


def check_direct_walk(checker: 'BorrowChecker', node: s.Loop, source: s.Items) -> None:
    """The receiver, the freeze of its owner, the binder, then the body to its fixed point."""
    from .statements import check_loop_body

    iterable = cast(e.Call, source.iterable)
    receiver = _receiver(checker, iterable)
    arms.check(checker, receiver)
    clear_borrows(checker)
    temporary = not names_an_owner(checker, receiver)
    frame: list[tuple[Owner, Owner]] = []
    walk = None if temporary else _freeze_owner(checker, node, receiver, iterable, frame)
    item = () if isinstance(source.binder, s.Discard) else (source.binder,)
    if item:
        _declare_item(checker, node, source, receiver, temporary, frame)
        if walk is not None:
            checker.borrow_state[source.binder].covered_by = walk.local

    def at_the_back_edge() -> None:
        if walk is not None and walk.invalidated_at is not None:
            emit_change_under_iterator(checker, walk, iterable, node.loc)

    try:
        check_loop_body(checker, node.body, at_the_back_edge, per_round=frozenset(item),
                        walk=walk)
    finally:
        for owner, binding in frame:
            release_binding_borrow(checker.borrow_state.get(owner), binding)
        if walk is not None:
            checker.borrow_state.pop(walk.local, None)


def check_value_walk(checker: 'BorrowChecker', node: s.Loop, source: s.Items) -> None:
    """The iterable, the freeze of what it lends, the binder, then the body."""
    from .statements import check_loop_body

    iterable = source.iterable
    arms.check(checker, iterable)
    owners = lent_owners(checker, iterable)
    if walks_in_place(checker, node, iterable):
        reject_readonly_write(checker, borrow_owner(checker, iterable), iterable.loc,
                              f"walk '{spelled(checker, iterable)}'", receiver=iterable)
    elif owners is None:
        consume(checker, iterable)
    else:
        decide(checker, iterable, Provenance.FRESH, source_class(checker, iterable))
    clear_borrows(checker)
    frame: list[tuple[Owner, Owner]] = []
    walk = _freeze_owners(checker, node, iterable, owners or (), frame)
    item = () if isinstance(source.binder, s.Discard) else (source.binder,)
    if item:
        _declare_value_item(checker, node, source, owners or (), frame)
        if walk is not None:
            checker.borrow_state[source.binder].covered_by = walk.local

    def at_the_back_edge() -> None:
        if walk is not None and walk.invalidated_at is not None:
            emit_change_under_iterator(checker, walk, iterable, node.loc)

    try:
        check_loop_body(checker, node.body, at_the_back_edge, per_round=frozenset(item),
                        walk=walk)
    finally:
        for owner, binding in frame:
            release_binding_borrow(checker.borrow_state.get(owner), binding)
        if walk is not None:
            checker.borrow_state.pop(walk.local, None)


def _freeze_owners(checker: 'BorrowChecker', node: s.Loop, iterable: e.ShirExpr,
                   owners: tuple['Owner', ...], frame: list[tuple['Owner', 'Owner']]
                   ) -> Optional[BorrowState]:
    """The state of a walk that lends: it freezes each owner for the whole loop."""
    held = tuple(owner for owner in owners if checker.borrow_state.get(owner) is not None)
    if not held:
        return None
    walk = BorrowState(name=f"<walk {node.shir_id.index}>", local=f"<walk {node.shir_id.index}>",
                       bound_at_span=iterable.loc)
    checker.declare(walk)
    record_owners(checker.borrow_state, walk, held, iterable.loc, frame)
    return walk


def _declare_value_item(checker: 'BorrowChecker', node: s.Loop, source: s.Items,
                        owners: tuple['Owner', ...],
                        frame: list[tuple['Owner', 'Owner']]) -> None:
    """The binder of a value walk: a read-only view of a lent item, else an owned local of
    the body."""
    binder = checker.locals.get(source.binder)
    at = node.loc if binder is None or binder.loc is None else binder.loc
    if owns_its_items(checker.answers().type_of(source.iterable.shir_id)):
        checker.declare(BorrowState(name=checker.name_of(source.binder), local=source.binder,
                                    var_type=checker.local_type(source.binder),
                                    declared_at_span=at))
        return
    declare_bare_binders(checker, [(source.binder, at)], {source.binder: owners}, node.body,
                         frame)


def _receiver(checker: 'BorrowChecker', iterable: e.Call) -> e.ShirExpr:
    """The container of a direct walk: the receiver of its producer."""
    resolved = checker.answers().callee_of(iterable.shir_id)
    return cast(Builtin, resolved.callee if resolved is not None else None).recv


def _freeze_owner(checker: 'BorrowChecker', node: s.Loop, receiver: e.ShirExpr,
                  iterable: e.ShirExpr, frame: list[tuple['Owner', 'Owner']]
                  ) -> Optional[BorrowState]:
    """The state of the walk, which freezes the owner of the container for the whole loop.
    None for a container that no local owns: a constant does not change."""
    owner = borrow_owner(checker, receiver)
    if owner is None or checker.borrow_state.get(owner) is None:
        return None
    walk = BorrowState(name=f"<walk {node.shir_id.index}>", local=f"<walk {node.shir_id.index}>",
                       bound_at_span=iterable.loc, views_storage_of=receiver)
    checker.declare(walk)
    record_owners(checker.borrow_state, walk, (owner,), iterable.loc, frame)
    return walk


def _declare_item(checker: 'BorrowChecker', node: s.Loop, source: s.Items,
                  receiver: e.ShirExpr, temporary: bool,
                  frame: list[tuple['Owner', 'Owner']]) -> None:
    """The binder of the walk, in the state of its mode."""
    binder = checker.locals.get(source.binder)
    at = node.loc if binder is None or binder.loc is None else binder.loc
    if isinstance(source.by, s.Bare):
        owners = {source.binder: () if temporary else value_owners(checker, receiver)}
        declare_bare_binders(checker, [(source.binder, at)], owners, node.body, frame)
        return
    written = source.by.loc or at
    ty = checker.local_type(source.binder)
    state = BorrowState(name=checker.name_of(source.binder), local=source.binder,
                        var_type=None if ty is None else ReferenceType(ty, source.by.mode),
                        bound_at_span=at, declared_at_span=written)
    checker.declare(state)
    if not _refuses_a_poke_item(checker, source, receiver, written, temporary) and not temporary:
        freeze_reference(checker, state, receiver, written, writes=False, frame=frame,
                         bound_at=at)
    note_binder(checker, state, node.body)


def _refuses_a_poke_item(checker: 'BorrowChecker', source: s.Items, receiver: e.ShirExpr,
                         span: Optional[Span], temporary: bool) -> bool:
    """A `poke` item is a write position, so its container must be writable. A slice is
    CE2426, and the loop still binds the item. A read-only root is the answer of the
    read-only gate, and the item then freezes nothing."""
    if not (isinstance(source.by, s.Ref) and source.by.mode is BorrowMode.POKE):
        return False
    what = "take a `poke` item"
    if reject_write_through_slice(checker, receiver, span, what) or temporary:
        return False
    return reject_readonly_write(checker, borrow_owner(checker, receiver), span, what)
