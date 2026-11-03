"""The statement arms of the borrow pass over SHIR (docs/design/ir.md 7.5, 7.9).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`arms.ARMS`), and releases the `let`-borrows that the block opened. The
borrows of a statement end with the statement (`clear_borrows`).

An `Assign` of a local is a `let` (`is_init`) or a rebind. A `let` BINDS: it declares the
local, then takes its value by the decision of a consuming use (`bind`). A rebind
RE-INITIALIZES the local: it is refused through a view of another value's storage
(`reject_readonly_write`), it takes its value as a consuming use, and every fact about the
old value goes. A write of a field changes its root in place: it is refused through a
read-only view of the root, and it takes its value as a consuming use. A destructure
takes its value as a consuming use, declares its binders, and rebinds its places from left
to right. A `return` hands its value to the caller, a consuming use. An expression
statement reads its value. The `Assign` of an element is checked by the element group,
and the `return` of a `peek` return by its own group: each stops the walk.

An `if` checks each block from the state before it, and the paths that reach the code
after it join. A `match` reads its scrutinee (`match nom r:` takes it), each arm declares
its bare binders, and its arms join as the blocks of an `if` do. A `while` loop checks its
condition once, then its body in two rounds (`check_loop_body`): the paths of a
`continue` and of the end of the body go round the back edge, and the paths of a `break`
leave the loop. A loop over items is
checked by the iterators group, and it stops the walk. The end of a block that the path
reaches, a `return`, a `break` and a `continue` are exits of the blocks that they leave
(`flow.record_exit`), and a rebind is an exit of the old value of its local
(`flow.record_rebind`).
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span

from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.paths import terminates
from sushi_lang.semantics.typesys import ReferenceType

from . import arms
from .bindings import bare_binders, declare_bare_binders, release_binding_borrow
from .borrows import LATER_ROOTS, clear_borrows
from .consume import bind, binds_a_bare_literal_string, consume
from .expressions import reject_a_use_after_the_change
from .flow import (
    FlowFacts,
    LoopFrame,
    record_exit,
    record_rebind,
    reinitialize,
    restore_flow,
    snapshot_flow,
)
from .reads import borrow_owner, read_type
from .state import BorrowState
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker


def check_block(checker: 'BorrowChecker', block: ShirBlock) -> None:
    """Check a block, releasing the `let`-borrows it opened on the way out.

    A statement of an unrolled `expand` copy that ends every path, with more statements
    after it, leaves the rest of the block on no path (ir.md 7.2): the group of the
    unrolled copies checks such a block, and the walk of the body stops there.
    """
    copies = checker.body.copies if checker.body is not None else None
    checker._scope_binding_borrows.append([])
    checker.block_locals.append([])
    try:
        for index, statement in enumerate(block.statements):
            arms.check(checker, statement)
            if (copies is not None and copies.holding(block.shir_id, index)
                    and index + 1 < len(block.statements)
                    and terminates(statement, leaves_round=True)):
                raise StageStop(arms.STAGE, "Expand", statement.loc)
        if not terminates(block, leaves_round=True):
            record_exit(checker, len(checker.block_locals) - 1)
    finally:
        checker.block_locals.pop()
        for owner, binding in checker._scope_binding_borrows.pop():
            release_binding_borrow(checker.borrow_state.get(owner), binding)


def check_assign(checker: 'BorrowChecker', node: s.Assign) -> None:
    if isinstance(node.target, e.Field):
        _check_field_write(checker, node, node.target)
    elif not isinstance(node.target, e.LocalRef):
        arms.stop_at(checker, node)
    elif node.is_init:
        _check_let(checker, node, node.target.local)
    else:
        _check_rebind(checker, node, node.target.local)
    clear_borrows(checker)


def _check_let(checker: 'BorrowChecker', node: s.Assign, local: LocalId) -> None:
    """`let T x = v`: declare the local, then give it the provenance of its value."""
    if isinstance(node.value, e.Borrow):
        # `let poke T x = <place>` (#409): a borrow binding, not a value.
        arms.stop_at(checker, node)
    ty = checker.local_type(local)
    state = BorrowState(name=checker.name_of(local), local=local, var_type=ty,
                        declared_at_span=node.loc,
                        # Option B: a string bound straight from a literal owns no heap,
                        # so consuming it transfers nothing and CE2405 must not fire on it.
                        owns_no_heap=binds_a_bare_literal_string(ty, node.value))
    checker.declare(state)
    arms.check(checker, node.value)
    bind(checker, node, state)
    # The local is in its scope from here: an exit in its value comes before it.
    checker.block_locals[-1].append(local)


def _check_rebind(checker: 'BorrowChecker', node: s.Assign, local: LocalId) -> None:
    """`x := v`: the local takes ownership of a new value."""
    # No "rebind while borrowed" check here, deliberately: this runs BEFORE the value
    # walk, and moving it after would reject `x := f(peek x)`. CE2401 lives at the
    # consuming use instead.
    _reject_readonly_rebind(checker, node.target, node.loc, node.value)
    arms.check(checker, node.value)
    # Replacing what the owner holds invalidates every binding reading out of it (#242).
    check_owner_not_borrowed(checker, local, node.loc, "assign", place=node.target)
    consume(checker, node.value)
    _reinitialize(checker, local)


def _reject_readonly_rebind(checker: 'BorrowChecker', target: e.LocalRef,
                            span: Optional[Span], value: Optional[e.ShirExpr]) -> None:
    """The gates of a rebind of a local, before its value. `value` is None for a place of a
    destructure, which takes an element and never a literal."""
    state = checker.borrow_state.get(target.local)
    if state is None:
        return
    # THE gate, in the rebind position: a name that is a view of another value's storage
    # cannot be rebound, a name with storage of its own can. A `poke` reference is the
    # middle case and stays legal -- the store goes through the pointer, which is what
    # the mode is for.
    refused = reject_readonly_write(checker, target.local, span, "rebind the name",
                                    rebind=True)
    # The store goes through the pointer, so it is a USE of the reference: after a change
    # of its owner it writes into storage the owner no longer holds.
    if not refused and isinstance(state.var_type, ReferenceType):
        reject_a_use_after_the_change(checker, target.local, target.loc)
    # Option B: RE-DERIVE, never inherit. A rebind can only CLEAR this flag, never set it
    # on a value that owns heap.
    state.owns_no_heap = binds_a_bare_literal_string(state.var_type, value)


def _reinitialize(checker: 'BorrowChecker', local: LocalId) -> None:
    """The rebind drops the old value, so it is an exit of that value: one moved on some
    paths only needs the drop flag (`flow.record_rebind`). A rebind RE-INITIALIZES, so a
    previous move no longer holds. The value was checked before, so `s := "{s}-x"` still
    reports the moved `s`."""
    record_rebind(checker, local)
    state = checker.borrow_state.get(local)
    if state is not None:
        reinitialize(state)


def _check_field_write(checker: 'BorrowChecker', node: s.Assign, target: e.Field) -> None:
    """`p.x := v`: a write of a field changes its root in place, and takes the value as a
    consuming use."""
    _reject_readonly_field_write(checker, target, node.loc)
    arms.check(checker, node.value)
    # Replacing what the owner holds invalidates every binding reading out of it (#242).
    check_owner_not_borrowed(checker, borrow_owner(target), node.loc, "assign",
                             place=target)
    consume(checker, node.value)


def _reject_readonly_field_write(checker: 'BorrowChecker', target: e.Field,
                                 span: Optional[Span]) -> None:
    """The gate of a write of a field, then the read of the field. A write is allowed
    unless its root is a read-only view, where the store cannot reach what it writes, or
    a temporary (CE2429). A root that a later group checks stops the walk."""
    root = walk_place(target, Step.MEMBER).node
    if isinstance(root, LATER_ROOTS):
        arms.stop_at(checker, root)
    reject_readonly_write(checker, borrow_owner(target), span, "assign to a field",
                          receiver=target.base)
    arms.check(checker, target)


def check_destructure(checker: 'BorrowChecker', node: s.Destructure) -> None:
    """`let (a, b) = v` and `(a, b) := v` of plain values (docs/design/ir.md 7.5).

    The value is checked first, and it is a consuming use: a plain value is a copy. A `let`
    declares each binder as a local of its own, at any nesting level. A rebind form then
    rebinds each place from left to right, through the gates of a rebind of a local or of
    a write of a field, at the span of the place. A value that owns stops the walk after
    the gates of the places: each binder owns or borrows an element of it, and a later
    group checks that.
    """
    arms.check(checker, node.value)
    owns = checker.types.type_class(read_type(checker, node.value)) is TypeClass.MOVE
    if not owns:
        consume(checker, node.value)
    targets = _targets(node.targets)
    for target in targets:
        if isinstance(target, s.Place):
            _rebind_place(checker, target.target)
    if owns:
        arms.stop_at(checker, node)
    for target in targets:
        if not isinstance(target, s.Place):
            _declare_binder(checker, target)
    clear_borrows(checker)


def _targets(targets: Sequence[s.DestructureTarget]) -> list[LocalId | s.Place]:
    """The binders and the places of a destructure, from left to right, at any nesting
    level. A `_` binds nothing."""
    found: list[LocalId | s.Place] = []
    for target in targets:
        if isinstance(target, s.Nested):
            found.extend(_targets(target.targets))
        elif not isinstance(target, s.Discard):
            found.append(target)
    return found


def _declare_binder(checker: 'BorrowChecker', local: LocalId) -> None:
    shir_local = checker.locals.get(local)
    checker.declare(BorrowState(name=checker.name_of(local), local=local,
                                var_type=checker.local_type(local),
                                declared_at_span=shir_local.loc if shir_local else None))
    checker.block_locals[-1].append(local)


def _rebind_place(checker: 'BorrowChecker', place: e.ShirExpr) -> None:
    """A place of a rebind form takes its element: a local is rebound, and a field is
    written. Typecheck gives a place of no other kind no answer, and stops first."""
    if isinstance(place, e.Field):
        _reject_readonly_field_write(checker, place, place.loc)
        check_owner_not_borrowed(checker, borrow_owner(place), place.loc, "assign",
                                 place=place)
        return
    if not isinstance(place, e.LocalRef):
        arms.stop_at(checker, place)
    _reject_readonly_rebind(checker, place, place.loc, None)
    check_owner_not_borrowed(checker, place.local, place.loc, "assign", place=place)
    _reinitialize(checker, place.local)


def check_return(checker: 'BorrowChecker', node: s.Return) -> None:
    """A `return` hands its value to the caller: a consuming use."""
    if checker.borrowed_return:
        # A `peek` body hands a VIEW to the caller (#1229): its group checks it.
        arms.stop_at(checker, node)
    arms.check(checker, node.value)
    consume(checker, node.value)
    clear_borrows(checker)
    record_exit(checker)


def check_expr_stmt(checker: 'BorrowChecker', node: s.ExprStmt) -> None:
    """A value that the statement reads and drops."""
    arms.check(checker, node.value)
    clear_borrows(checker)


def check_assert(checker: 'BorrowChecker', node: s.Assert) -> None:
    """The condition is read at the entry. The message is read only on the failing path, so
    the path that holds skips it and the two paths join."""
    arms.check(checker, node.cond)
    clear_borrows(checker)
    if node.message is None:
        return
    entry = snapshot_flow(checker)
    arms.check(checker, node.message)
    clear_borrows(checker)
    restore_flow(checker, FlowFacts.join([entry, snapshot_flow(checker)]))


def join_exclusive_paths(checker: 'BorrowChecker', blocks: Sequence[ShirBlock | None],
                         enter: Optional[Callable[[int], None]] = None) -> None:
    """Check exclusive blocks, each from the state before them; the paths that reach the
    code after them JOIN. `None` is a path that skips every block. `enter` runs at the
    start of each path, with the index of its block: an arm of a match declares its
    binders there.

    A local is moved after the join when it is moved on ANY path, and moved on every path
    when it is moved on each one. Only the paths that reach the code after the blocks
    contribute: a block that ends in a `return`, a `break` or a `continue` leaves by its
    own exit, so its move reaches no other block and no statement below (#287).
    """
    entry = snapshot_flow(checker)
    paths: list[FlowFacts] = []
    for index, block in enumerate(blocks):
        restore_flow(checker, entry)
        if block is None:
            paths.append(entry)
            continue
        if enter is not None:
            enter(index)
        check_block(checker, block)
        if not terminates(block, leaves_round=True):
            paths.append(snapshot_flow(checker))
    restore_flow(checker, FlowFacts.join(paths))


def check_if(checker: 'BorrowChecker', node: s.If) -> None:
    """The two blocks are exclusive paths. With no `else`, the path that skips the `then`
    block is a path too. An `elif` is an `if` in the `else` block."""
    arms.check(checker, node.cond)
    clear_borrows(checker)
    join_exclusive_paths(checker, (node.then, node.otherwise))


def check_match(checker: 'BorrowChecker', node: s.Match) -> None:
    """A match reads its scrutinee, and `match nom r:` takes it over, a consuming use. The
    arms are exclusive paths, and they join as the blocks of an `if` do. The arms cover
    each value (the typecheck pass), so no path skips them. Each arm declares its bare
    binders of plain payloads and plain tuple parts before its body. A match with a binder
    of another kind, with a bare binder of a payload or part that owns, or with a tuple
    literal that owns as its scrutinee, stops the walk: later groups check it. A tuple
    literal of plain parts is checked as any tuple: each part is a copy."""
    binders = [bare_binders(arm.pattern) for arm in node.arms]
    if any(found is None for found in binders):
        arms.stop_at(checker, node)
    if any(checker.types.type_class(checker.local_type(local)) is TypeClass.MOVE
           for found in binders for local, _at in found or ()):
        arms.stop_at(checker, node)
    if (isinstance(node.scrutinee, e.TupleInit) and checker.types.type_class(
            read_type(checker, node.scrutinee)) is TypeClass.MOVE):
        arms.stop_at(checker, node)
    arms.check(checker, node.scrutinee)
    if node.consumes:
        consume(checker, node.scrutinee)
    clear_borrows(checker)
    join_exclusive_paths(checker, [arm.body for arm in node.arms],
                         enter=lambda index: declare_bare_binders(checker,
                                                                  binders[index] or ()))


def check_loop(checker: 'BorrowChecker', node: s.Loop) -> None:
    """`while (cond)`: the condition is read once, at the entry, then the body is checked
    to its fixed point. A loop over items stops the walk: the iterators group checks it."""
    if not isinstance(node.source, s.Cond):
        arms.stop_at(checker, node)
    arms.check(checker, node.source.cond)
    clear_borrows(checker)
    check_loop_body(checker, node.body)


def check_loop_body(checker: 'BorrowChecker', body: ShirBlock) -> None:
    """Check a loop body to a fixed point, so that the back edge is honoured.

    The first round discovers the facts that come round the back edge: it reports no
    error, writes no row and records no exit. The second round starts from the entry
    joined with that back edge, and reports. The code after the loop sees the fixed point
    (the condition is false) joined with each `break` path of the second round (#993).
    """
    entry = snapshot_flow(checker)
    suppressed = checker.err.suppressed
    checker.err.suppressed = True
    checker.discovery_rounds += 1
    try:
        first_back, _first_breaks = _check_round(checker, body)
    finally:
        checker.discovery_rounds -= 1
        checker.err.suppressed = suppressed
    fixed_point = entry if first_back is None else entry | first_back
    restore_flow(checker, fixed_point)
    _back, breaks = _check_round(checker, body)
    restore_flow(checker, fixed_point if breaks is None else fixed_point | breaks)


def _check_round(checker: 'BorrowChecker', body: ShirBlock
                 ) -> tuple[FlowFacts | None, FlowFacts | None]:
    """Check one round of a loop body: the join of the paths to the back edge, and the
    join of the `break` paths. Each is None when no path of that kind exists, because
    `FlowFacts.join` of no path is no identity."""
    frame = LoopFrame(len(checker.block_locals))
    checker.loop_frames.append(frame)
    try:
        check_block(checker, body)
    finally:
        checker.loop_frames.pop()
    back = list(frame.continues)
    if not terminates(body, leaves_round=True):
        back.append(snapshot_flow(checker))
    return (FlowFacts.join(back) if back else None,
            FlowFacts.join(frame.breaks) if frame.breaks else None)


def check_break(checker: 'BorrowChecker', node: s.Break) -> None:
    """A `break` path leaves the loop: it joins the code after the loop."""
    _innermost_loop(checker, node).breaks.append(_leave_round(checker, node))


def check_continue(checker: 'BorrowChecker', node: s.Continue) -> None:
    """A `continue` path goes round the back edge to the next round."""
    _innermost_loop(checker, node).continues.append(_leave_round(checker, node))


def _leave_round(checker: 'BorrowChecker', node: s.Break | s.Continue) -> FlowFacts:
    """The exit of each block of the loop body, and the facts of the path."""
    record_exit(checker, _innermost_loop(checker, node).blocks)
    return snapshot_flow(checker)


def _innermost_loop(checker: 'BorrowChecker', node: s.Break | s.Continue) -> LoopFrame:
    if not checker.loop_frames:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"a '{type(node).__name__}' outside a loop reached "
                                       f"the borrow pass; the typecheck pass refuses it")
    return checker.loop_frames[-1]
