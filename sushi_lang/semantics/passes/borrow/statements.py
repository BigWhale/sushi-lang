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
from sushi_lang.semantics.typesys import BorrowMode, ForeignPtrType, ReferenceType

from .bindings import (
    freeze_ref_binding_owner,
    register_binding,
    register_pattern_bindings,
    release_frozen,
    restore_displaced,
)
from .borrows import clear_borrows
from .consume import bind, binds_a_bare_literal_string, consume, reconcile_closure_bind
from .flow import FlowFacts, reinitialize, terminates
from .reads import root_owner
from .state import BorrowState
from .writes import check_owner_not_borrowed, reject_readonly_write

if TYPE_CHECKING:
    from . import BorrowChecker


def check_block(checker: 'BorrowChecker', block: Block) -> None:
    """Check borrow safety for a block of statements."""
    checker._scope_binding_borrows.append([])
    try:
        for stmt in block.statements:
            check_stmt(checker, stmt)
    finally:
        for owner, binding in checker._scope_binding_borrows.pop():
            state = checker.borrow_state.get(owner)
            if state is not None:
                state.binding_borrows = [
                    entry for entry in state.binding_borrows if entry[0] != binding
                ]


def check_loop_body(checker: 'BorrowChecker', body: Block) -> None:
    """Borrow-check a loop body to a fixed point so the back edge is honoured."""
    entry = checker._snapshot_flow()
    prev_suppressed = checker.err.suppressed
    checker.err.suppressed = True
    check_block(checker, body)
    checker.err.suppressed = prev_suppressed
    fixed_point = entry | checker._snapshot_flow()
    checker._restore_flow(fixed_point)
    check_block(checker, body)
    checker._restore_flow(fixed_point)


def check_stmt(checker: 'BorrowChecker', stmt: Stmt) -> None:
    """Check borrow safety for a single statement."""
    if isinstance(stmt, Let):
        if isinstance(stmt.ty, ForeignPtrType):
            # A foreign `ptr` is exempt from ALIASING analysis but NOT from the
            # ownership stamp: skipping the stamp is CE0129 on the first FFI program
            # that binds one.
            checker.borrow_state[stmt.name] = BorrowState(
                name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc)
            bind(checker, stmt)
            clear_borrows(checker)
            return
        checker.borrow_state[stmt.name] = BorrowState(
            name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc,
            # Option B: a string bound straight from a literal owns no heap,
            # so consuming it transfers nothing and CE2405 must not fire on it.
            owns_no_heap=binds_a_bare_literal_string(stmt.ty, stmt.value))
        checker._check_expr(stmt.value)
        reconcile_closure_bind(checker, stmt)
        # A `let` BINDS; it does not take ownership (#242). It inherits the source's
        # provenance, so a read through a live owner makes it a BORROW.
        bind(checker, stmt)
        clear_borrows(checker)

    elif isinstance(stmt, Rebind):
        # Variable or field rebinding - check if source is borrowed
        # For simple rebind (x := value), target is a Name
        # For field rebind (obj.field := value), target is a MemberAccess
        if isinstance(stmt.target, Name):
            var_name = stmt.target.id
            if var_name in checker.borrow_state:
                state = checker.borrow_state[var_name]

                if isinstance(state.var_type, ReferenceType):
                    if state.var_type.is_peek():
                        checker.err.emit(er.ERR.CE2408, stmt.loc, name=var_name)
                    # poke references allow rebind (mutable reference semantics)
                #
                # No "rebind while borrowed" check here, deliberately: this runs BEFORE
                # the value walk, and moving it after would reject `x := f(peek x)`.
                # CE2401 lives at the consuming use instead.

                # Option B: RE-DERIVE, never inherit. A rebind can only CLEAR this
                # flag, never set it on a value that owns heap.
                state.owns_no_heap = binds_a_bare_literal_string(
                    state.var_type, stmt.value)

        elif isinstance(stmt.target, MemberAccess):
            # A field rebind mutates in place, so it is allowed unless the root owner
            # is a read-only receiver, where the store cannot reach what it writes.
            reject_readonly_write(checker, root_owner(stmt.target), stmt.loc,
                                  "assign to a field")
            checker._check_expr(stmt.target)

        checker._check_expr(stmt.value)
        # Both rebind shapes take ownership, and replacing what the owner holds
        # invalidates every binding reading out of it (#242).
        check_owner_not_borrowed(checker, root_owner(stmt.target), stmt.loc, "assign")
        if isinstance(stmt.target, Name):
            consume(checker, stmt.value, ConsumingUse.REBIND)
            # A rebind RE-INITIALIZES, so a previous move no longer holds. The value
            # was checked above, so `s := "{s}-x"` still reports the moved `s`.
            target_state = checker.borrow_state.get(stmt.target.id)
            if target_state is not None:
                reinitialize(target_state)
        elif isinstance(stmt.target, MemberAccess):
            consume(checker, stmt.value, ConsumingUse.FIELD_ASSIGN)
        clear_borrows(checker)

    elif isinstance(stmt, Return):
        checker._check_expr(stmt.value)
        # A return hands the value to the caller. `return Result.Ok(x)` consumes `x`
        # at ENUM_PAYLOAD and the constructor itself is FRESH, so this matters for the
        # shape that is not wrapped: an extension method's bare `return value`.
        consume(checker, stmt.value, ConsumingUse.RETURN)
        clear_borrows(checker)

    elif isinstance(stmt, Print) or isinstance(stmt, PrintLn):
        checker._check_expr(stmt.value)
        clear_borrows(checker)

    elif isinstance(stmt, ExprStmt):
        checker._check_expr(stmt.expr)
        # `x.destroy()` releases what `x` holds, so every later use is CE2406.
        #
        # No "destroy while borrowed" check (the retired CE2402): `.destroy()` returns
        # `~`, so it is always its own statement and the counters are already clear.
        # CE2408, CE2412 and CE2406 cover its intent.
        if isinstance(stmt.expr, (MethodCall, DotCall)):
            if stmt.expr.method == "destroy":
                if isinstance(stmt.expr.receiver, Name):
                    state = checker.borrow_state.get(stmt.expr.receiver.id)
                    if state is not None:
                        state.is_destroyed = True
        clear_borrows(checker)

    elif isinstance(stmt, If):
        # Every arm starts from the pre-if state and the results JOIN: moved after the
        # `if` iff moved on ANY path. Without the snapshot a move leaks into siblings.
        entry = checker._snapshot_flow()
        # Only the paths that REACH the code after the `if` contribute to the join. An
        # arm that ends in `return` leaves the function, so its move cannot reach a
        # sibling arm or the statements below (#287).
        paths: list[FlowFacts] = []
        for cond_expr, arm_block in stmt.arms:
            checker._restore_flow(entry)
            checker._check_expr(cond_expr)
            clear_borrows(checker)
            check_block(checker, arm_block)
            if not terminates(arm_block):
                paths.append(checker._snapshot_flow())
        if stmt.else_block:
            checker._restore_flow(entry)
            check_block(checker, stmt.else_block)
            if not terminates(stmt.else_block):
                paths.append(checker._snapshot_flow())
        else:
            paths.append(entry)
        checker._restore_flow(FlowFacts.join(paths))

    elif isinstance(stmt, While):
        checker._check_expr(stmt.cond)
        clear_borrows(checker)
        check_loop_body(checker, stmt.body)

    elif isinstance(stmt, Foreach):
        checker._check_expr(stmt.iterable)
        clear_borrows(checker)
        # The loop variable BORROWS the element, matching the backend's
        # `register_cleanup=False`. It lives for the LOOP and no longer (#337), so it
        # goes through the displaced-entry bracket and an outer local it shadows gets
        # its state back.
        displaced: dict = {}
        frozen: list = []
        span = stmt.item_name_span or stmt.loc
        if stmt.item_borrow is not None:
            # A reference binding (#300): the state carries the full `ReferenceType`,
            # which wires every rule in by construction. NOT `is_borrowed_binding` --
            # that would put a `poke` binding in the CE2414 row and reject the write
            # the marker exists to allow.
            mode = BorrowMode.POKE if stmt.item_borrow == "poke" else BorrowMode.PEEK
            state = BorrowState(
                name=stmt.item_name,
                var_type=ReferenceType(stmt.item_type, mode),
                bound_at_span=span, declared_at_span=stmt.item_borrow_span or span,
            )
            register_binding(checker, stmt.item_name, state, displaced)
            freeze_ref_binding_owner(checker, state, stmt.iterable, span, frozen,
                                     poke_span=stmt.item_borrow_span)
        else:
            register_binding(
                checker,
                stmt.item_name,
                BorrowState(
                    name=stmt.item_name, var_type=stmt.item_type,
                    is_borrowed_binding=True,
                    bound_at_span=span,
                ),
                displaced,
            )
        try:
            check_loop_body(checker, stmt.body)
        finally:
            restore_displaced(checker, displaced)
            release_frozen(checker, frozen)

    elif isinstance(stmt, Match):
        checker._check_expr(stmt.scrutinee)
        clear_borrows(checker)
        # Match arms are EXCLUSIVE paths, so they take the same snapshot / restore /
        # join as the `If` arm above.
        entry = checker._snapshot_flow()
        paths: list[FlowFacts] = []
        for arm in stmt.arms:
            checker._restore_flow(entry)
            # The scrutinee type gives each binding its var_type; Pass 2 stamps it
            # (CE0121 guards that).
            #
            # A binding lives for its ARM and no longer (#337), so registration goes
            # through the displaced-entry bracket. The restore runs BEFORE the path
            # snapshot, so the join sees the outer local's facts, never the
            # binding's.
            displaced: dict = {}
            frozen: list = []
            if isinstance(arm.pattern, Pattern):
                register_pattern_bindings(
                    checker, arm.pattern, stmt.resolved_scrutinee_type, displaced,
                    scrutinee=stmt.scrutinee, frozen=frozen)
            try:
                if isinstance(arm.body, Block):
                    check_block(checker, arm.body)
                else:
                    checker._check_expr(arm.body)
                    clear_borrows(checker)
            finally:
                restore_displaced(checker, displaced)
                release_frozen(checker, frozen)
            if not terminates(arm.body):
                paths.append(checker._snapshot_flow())
        # A `match` is exhaustive (Pass 2 enforces it), so unlike an `if` with no else
        # there is no fall-through path to add: some arm always runs.
        checker._restore_flow(FlowFacts.join(paths))

    elif isinstance(stmt, Break) or isinstance(stmt, Continue):
        pass  # No borrow checking needed
