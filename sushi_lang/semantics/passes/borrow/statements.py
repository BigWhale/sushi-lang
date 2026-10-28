"""The statement arms of the borrow pass over SHIR (docs/design/ir.md 7.5, 7.9).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`arms.ARMS`), and releases the `let`-borrows that the block opened. The
borrows of a statement end with the statement (`clear_borrows`).

An `Assign` of a local is a `let` (`is_init`) or a rebind. A `let` BINDS: it declares the
local, then takes its value by the decision of a consuming use (`bind`). A rebind
RE-INITIALIZES the local: it is refused through a view of another value's storage
(`reject_readonly_write`), it takes its value as a consuming use, and every fact about the
old value goes. A `return` hands its value to the caller, a consuming use. An expression
statement reads its value. The `Assign` of any other place is checked by the field and
element groups, and the `return` of a `peek` return by its own group: each stops the
walk.

An `if` checks each block from the state before it, and the paths that reach the code
after it join. A `while` loop checks its condition once, then its body in two rounds
(`check_loop_body`): the paths of a `continue` and of the end of the body go round the
back edge, and the paths of a `break` leave the loop. A loop over items is checked by the
iterators group, and it stops the walk. The end of a block that the path reaches, a
`return`, a `break` and a `continue` are exits of the blocks that they leave
(`flow.record_exit`), and a rebind is an exit of the old value of its local
(`flow.record_rebind`).
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop

from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.paths import terminates
from sushi_lang.semantics.typesys import ReferenceType

from . import arms
from .bindings import release_binding_borrow
from .borrows import clear_borrows
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
    if not isinstance(node.target, e.LocalRef):
        arms.stop_at(checker, node)
    if node.is_init:
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
    state = checker.borrow_state.get(local)
    # No "rebind while borrowed" check here, deliberately: this runs BEFORE the value
    # walk, and moving it after would reject `x := f(peek x)`. CE2401 lives at the
    # consuming use instead.
    if state is not None:
        # THE gate, in the rebind position: a name that is a view of another value's
        # storage cannot be rebound, a name with storage of its own can. A `poke`
        # reference is the middle case and stays legal -- the store goes through the
        # pointer, which is what the mode is for.
        refused = reject_readonly_write(checker, local, node.loc, "rebind the name",
                                        rebind=True)
        # The store goes through the pointer, so it is a USE of the reference: after a
        # change of its owner it writes into storage the owner no longer holds.
        if not refused and isinstance(state.var_type, ReferenceType):
            reject_a_use_after_the_change(checker, local, node.target.loc)
        # Option B: RE-DERIVE, never inherit. A rebind can only CLEAR this flag, never
        # set it on a value that owns heap.
        state.owns_no_heap = binds_a_bare_literal_string(state.var_type, node.value)
    arms.check(checker, node.value)
    # Replacing what the owner holds invalidates every binding reading out of it (#242).
    check_owner_not_borrowed(checker, local, node.loc, "assign", place=node.target)
    consume(checker, node.value)
    # The rebind drops the old value, so it is an exit of that value: one moved on some
    # paths only needs the drop flag (`flow.record_rebind`).
    record_rebind(checker, local)
    # A rebind RE-INITIALIZES, so a previous move no longer holds. The value was checked
    # above, so `s := "{s}-x"` still reports the moved `s`.
    if state is not None:
        reinitialize(state)


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


def check_if(checker: 'BorrowChecker', node: s.If) -> None:
    """Each block starts from the state after the condition; the paths that reach the code
    after the `if` JOIN.

    A local is moved after the `if` when it is moved on ANY path, and moved on every path
    when it is moved on each one. Only the paths that reach the code after the `if`
    contribute: a block that ends in a `return`, a `break` or a `continue` leaves by its
    own exit, so its move reaches no other block and no statement below (#287). With no
    `else`, the path that skips the `then` block is a path too. An `elif` is an `if` in
    the `else` block.
    """
    arms.check(checker, node.cond)
    clear_borrows(checker)
    entry = snapshot_flow(checker)
    paths: list[FlowFacts] = []
    for block in (node.then, node.otherwise):
        restore_flow(checker, entry)
        if block is None:
            paths.append(entry)
            continue
        check_block(checker, block)
        if not terminates(block, leaves_round=True):
            paths.append(snapshot_flow(checker))
    restore_flow(checker, FlowFacts.join(paths))


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
