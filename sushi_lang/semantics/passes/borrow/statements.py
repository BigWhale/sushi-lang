"""Statement walking: scopes, branch joins, and the bindings each statement opens."""

from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    Block,
    Break,
    Continue,
    DotCall,
    ExprStmt,
    Foreach,
    If,
    IndexAccess,
    Let,
    Match,
    MemberAccess,
    MethodCall,
    Name,
    Pattern,
    Print,
    PrintLn,
    Rebind,
    Return,
    Stmt,
    While,
)
from sushi_lang.semantics.ownership import ConsumingUse
from sushi_lang.semantics.typesys import ForeignPtrType, ReferenceType

from .bindings import BindingScope, register_pattern_bindings, release_binding_borrow
from .borrows import clear_borrows
from .consume import bind, binds_a_bare_literal_string, consume, reconcile_closure_bind
from .expressions import check_expr
from .flow import FlowFacts, reinitialize, restore_flow, snapshot_flow, terminates
from .reads import root_owner
from .state import BorrowState
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from . import BorrowChecker


def check_block(checker: 'BorrowChecker', block: Block) -> None:
    """Check a block, releasing the `let`-borrows it opened on the way out."""
    checker._scope_binding_borrows.append([])
    try:
        for stmt in block.statements:
            check_stmt(checker, stmt)
    finally:
        for owner, binding in checker._scope_binding_borrows.pop():
            release_binding_borrow(checker.borrow_state.get(owner), binding)


def check_stmt(checker: 'BorrowChecker', stmt: Stmt) -> None:
    """Check borrow safety for a single statement."""
    match stmt:
        case Let():
            _check_let(checker, stmt)
        case Rebind():
            _check_rebind(checker, stmt)
        case Return():
            check_expr(checker, stmt.value)
            # A return hands the value to the caller. `return Result.Ok(x)` consumes `x`
            # at ENUM_PAYLOAD and the constructor itself is FRESH, so this matters for
            # the shape that is not wrapped: an extension method's bare `return value`.
            consume(checker, stmt.value, ConsumingUse.RETURN)
            clear_borrows(checker)
        case Print() | PrintLn():
            check_expr(checker, stmt.value)
            clear_borrows(checker)
        case ExprStmt():
            _check_expr_stmt(checker, stmt)
        case If():
            _check_if(checker, stmt)
        case While():
            check_expr(checker, stmt.cond)
            clear_borrows(checker)
            check_loop_body(checker, stmt.body)
        case Foreach():
            _check_foreach(checker, stmt)
        case Match():
            _check_match(checker, stmt)
        case Break() | Continue():
            pass  # No borrow checking needed


def _branch(checker: 'BorrowChecker'):
    """Count an if arm / match arm / loop body for conditional-move detection (#414)."""
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        checker.branch_depth += 1
        try:
            yield
        finally:
            checker.branch_depth -= 1
    return _scope()


def _check_let(checker: 'BorrowChecker', stmt: Let) -> None:
    """`let T x = v`: declare the binding, then give it its initializer's provenance."""
    checker.borrow_state[stmt.name] = BorrowState(
        name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc,
        declared_branch_depth=checker.branch_depth,
        # Option B: a string bound straight from a literal owns no heap, so consuming it
        # transfers nothing and CE2405 must not fire on it.
        owns_no_heap=binds_a_bare_literal_string(stmt.ty, stmt.value))
    if not isinstance(stmt.ty, ForeignPtrType):
        # A foreign `ptr` is exempt from ALIASING analysis but NOT from the ownership
        # stamp below: skipping the stamp is CE0129 on the first FFI program that binds
        # one.
        check_expr(checker, stmt.value)
        reconcile_closure_bind(checker, stmt)
    # A `let` BINDS; it does not take ownership (#242). It inherits the source's
    # provenance, so a read through a live owner makes it a BORROW.
    bind(checker, stmt)
    clear_borrows(checker)


def _check_rebind(checker: 'BorrowChecker', stmt: Rebind) -> None:
    """`x := v` or `obj.field := v`: the target takes ownership of a new value."""
    target = stmt.target
    owner = root_owner(target)

    # No "rebind while borrowed" check here, deliberately: this runs BEFORE the value
    # walk, and moving it after would reject `x := f(peek x)`. CE2401 lives at the
    # consuming use instead.
    if isinstance(target, Name):
        state = checker.borrow_state.get(target.id)
        if state is not None:
            # A `poke` reference allows a rebind -- that is what it is for.
            if isinstance(state.var_type, ReferenceType) and state.var_type.is_peek():
                checker.err.emit(er.ERR.CE2408, stmt.loc, name=target.id)
            # Option B: RE-DERIVE, never inherit. A rebind can only CLEAR this flag,
            # never set it on a value that owns heap.
            state.owns_no_heap = binds_a_bare_literal_string(state.var_type, stmt.value)
    elif isinstance(target, MemberAccess):
        # A field rebind mutates in place, so it is allowed unless the root owner is a
        # read-only receiver, where the store cannot reach what it writes.
        reject_readonly_write(checker, owner, stmt.loc, "assign to a field",
                              receiver=target.receiver)
        check_expr(checker, target)
    elif isinstance(target, IndexAccess):
        # An element rebind mutates in place too, and `root_owner` already walks an
        # index, so the same gate answers for all five read-only receiver kinds.
        reject_readonly_write(checker, owner, stmt.loc, "assign to an array element",
                              receiver=target.array)
        check_expr(checker, target)

    check_expr(checker, stmt.value)
    # Both rebind shapes take ownership, and replacing what the owner holds invalidates
    # every binding reading out of it (#242).
    check_owner_not_borrowed(checker, owner, stmt.loc, "assign")

    if isinstance(target, Name):
        consume(checker, stmt.value, ConsumingUse.REBIND)
        # A rebind RE-INITIALIZES, so a previous move no longer holds. The value was
        # checked above, so `s := "{s}-x"` still reports the moved `s`.
        target_state = checker.borrow_state.get(target.id)
        if target_state is not None:
            reinitialize(target_state)
    elif isinstance(target, MemberAccess):
        consume(checker, stmt.value, ConsumingUse.FIELD_ASSIGN)
    elif isinstance(target, IndexAccess):
        consume(checker, stmt.value, ConsumingUse.ELEMENT_ASSIGN)
    clear_borrows(checker)


def _check_expr_stmt(checker: 'BorrowChecker', stmt: ExprStmt) -> None:
    """A bare expression statement. `x.destroy()` releases what `x` holds."""
    check_expr(checker, stmt.expr)
    # Every later use of a destroyed value is CE2406.
    #
    # No "destroy while borrowed" check (the retired CE2402): `.destroy()` returns `~`,
    # so it is always its own statement and the counters are already clear. CE2408,
    # CE2412 and CE2406 cover its intent.
    if isinstance(stmt.expr, (MethodCall, DotCall)) and stmt.expr.method == "destroy":
        if isinstance(stmt.expr.receiver, Name):
            state = checker.borrow_state.get(stmt.expr.receiver.id)
            if state is not None:
                state.is_destroyed = True
    clear_borrows(checker)


def _check_if(checker: 'BorrowChecker', stmt: If) -> None:
    """Each arm starts from the pre-`if` state; the surviving paths JOIN."""
    # Moved after the `if` iff moved on ANY path. Without the snapshot a move leaks into
    # sibling arms. Only the paths that REACH the code after the `if` contribute: an arm
    # ending in `return` leaves the function, so its move cannot reach a sibling arm or
    # the statements below (#287).
    entry = snapshot_flow(checker)
    paths: list[FlowFacts] = []
    for cond_expr, arm_block in stmt.arms:
        restore_flow(checker, entry)
        check_expr(checker, cond_expr)
        clear_borrows(checker)
        with _branch(checker):
            check_block(checker, arm_block)
        if not terminates(arm_block):
            paths.append(snapshot_flow(checker))
    if stmt.else_block:
        restore_flow(checker, entry)
        with _branch(checker):
            check_block(checker, stmt.else_block)
        if not terminates(stmt.else_block):
            paths.append(snapshot_flow(checker))
    else:
        paths.append(entry)
    restore_flow(checker, FlowFacts.join(paths))


def _check_match(checker: 'BorrowChecker', stmt: Match) -> None:
    """Match arms are EXCLUSIVE paths, so they take the same snapshot / restore / join."""
    check_expr(checker, stmt.scrutinee)
    clear_borrows(checker)
    entry = snapshot_flow(checker)
    paths: list[FlowFacts] = []
    for arm in stmt.arms:
        restore_flow(checker, entry)
        # The scrutinee type gives each binding its var_type; Pass 2 stamps it (CE0121
        # guards that). The scope closes BEFORE the path snapshot, so the join sees the
        # outer local's facts, never the binding's.
        with BindingScope(checker) as scope, _branch(checker):
            if isinstance(arm.pattern, Pattern):
                register_pattern_bindings(checker, scope, arm.pattern,
                                          stmt.resolved_scrutinee_type,
                                          scrutinee=stmt.scrutinee)
            if isinstance(arm.body, Block):
                check_block(checker, arm.body)
            else:
                check_expr(checker, arm.body)
                clear_borrows(checker)
        if not terminates(arm.body):
            paths.append(snapshot_flow(checker))
    # A `match` is exhaustive (Pass 2 enforces it), so unlike an `if` with no else there
    # is no fall-through path to add: some arm always runs.
    restore_flow(checker, FlowFacts.join(paths))


def _check_foreach(checker: 'BorrowChecker', stmt: Foreach) -> None:
    """The loop variable BORROWS the element, and lives for the LOOP and no longer."""
    check_expr(checker, stmt.iterable)
    clear_borrows(checker)
    # A value binding matches the backend's `register_cleanup=False`; a reference binding
    # (#300) additionally freezes the container for the loop. Both end with the scope, so
    # an outer local the item shadows gets its state back (#337).
    span = stmt.item_name_span or stmt.loc
    with BindingScope(checker) as scope:
        if stmt.item_borrow is not None:
            scope.bind_ref(stmt.item_name, stmt.item_type, stmt.item_borrow, span,
                           owner=stmt.iterable, declared_at=stmt.item_borrow_span)
        else:
            scope.bind_value(stmt.item_name, stmt.item_type, span)
        check_loop_body(checker, stmt.body)


def check_loop_body(checker: 'BorrowChecker', body: Block) -> None:
    """Borrow-check a loop body to a fixed point so the back edge is honoured."""
    entry = snapshot_flow(checker)
    prev_suppressed = checker.err.suppressed
    checker.err.suppressed = True
    with _branch(checker):
        check_block(checker, body)
    checker.err.suppressed = prev_suppressed
    fixed_point = entry | snapshot_flow(checker)
    restore_flow(checker, fixed_point)
    with _branch(checker):
        check_block(checker, body)
    restore_flow(checker, fixed_point)
