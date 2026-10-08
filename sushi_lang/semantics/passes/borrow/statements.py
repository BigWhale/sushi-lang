"""Statement walking: scopes, branch joins, and the bindings each statement opens."""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.ast import (
    Assert,
    destructure_binders,
    Block,
    Break,
    Continue,
    DotCall,
    Expand,
    Expr,
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
    TupleLiteral,
    TuplePattern,
    While,
)
from sushi_lang.semantics.ownership import Provenance
from sushi_lang.semantics.typesys import ReferenceType

from .bindings import (
    bind_let_reference,
    BindingScope,
    freeze_for_a_view,
    register_pattern_bindings,
    reject_a_second_reference,
    ScrutineeKind,
    reject_partial_take,
    release_binding_borrow,
    walks_a_temporary,
)
from sushi_lang.semantics.generics.monomorphize.unroll import WrittenLet, written_let
from .borrows import clear_borrows
from .diagnostics import emit_change_under_iterator, expr_to_string
from .consume import (
    bind,
    binds_a_bare_literal_string,
    consume,
    reconcile_closure_bind,
    source_provenance,
)
from .expressions import check_expr, reject_a_use_after_the_change
from .flow import (
    FlowFacts,
    LoopFlow,
    LoopFrame,
    reinitialize,
    restore_flow,
    snapshot_flow,
    terminates,
)
from .reads import borrow_owner
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
            state = checker.borrow_state.get(stmt.name)
            if state is not None:
                state.written = written_let(stmt)
        case Rebind():
            _check_rebind(checker, stmt)
        case Return():
            check_expr(checker, stmt.value)
            # A return hands the value to the caller. `return Result.Ok(x)` consumes `x`
            # at ENUM_PAYLOAD and the constructor itself is FRESH, so this matters for
            # the shape that is not wrapped: an extension method's bare `return value`.
            consume(checker, stmt.value)
            clear_borrows(checker)
        case Print() | PrintLn():
            check_expr(checker, stmt.value)
            clear_borrows(checker)
        case Assert():
            check_expr(checker, stmt.cond)
            if stmt.message is not None:
                check_expr(checker, stmt.message)
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
        case Expand():
            _check_expand(checker, stmt)
        case Match():
            _check_match(checker, stmt)
        case Break():
            if checker._loop_frames:
                checker._loop_frames[-1].breaks.append(snapshot_flow(checker))
        case Continue():
            if checker._loop_frames:
                checker._loop_frames[-1].continues.append(snapshot_flow(checker))


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
    if isinstance(stmt.ty, ReferenceType):
        # `let poke T x = <place>` (#409): a borrow binding, not a value.
        bind_let_reference(checker, stmt)
        clear_borrows(checker)
        return
    checker.borrow_state[stmt.name] = BorrowState(
        name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc,
        declared_branch_depth=checker.branch_depth,
        # Option B: a string bound straight from a literal owns no heap, so consuming it
        # transfers nothing and CE2405 must not fire on it.
        owns_no_heap=binds_a_bare_literal_string(stmt.ty, stmt.value))
    # Every initializer is walked, a `let ptr` one too (#1175): a consuming use in it (a
    # cast, a constructor) is decided here like anywhere else. A foreign `ptr` VALUE is
    # exempt from aliasing analysis: its type owns nothing, so `bind` copies it.
    check_expr(checker, stmt.value)
    reconcile_closure_bind(checker, stmt)
    # A `let` BINDS; it does not take ownership (#242). It inherits the source's
    # provenance, so a read through a live owner makes it a BORROW.
    bind(checker, stmt)
    if stmt.targets is not None:
        _split_destructure(checker, stmt)
    clear_borrows(checker)


def _split_destructure(checker: 'BorrowChecker', stmt: Let) -> None:
    """`let (a, b) = v`: each binder is what the hidden whole is (ruling 4 of tuples).

    The hidden `Let` took the value by the rule of `let x = v`: it owns a temporary and an
    owned local (which it spends), and it borrows a parameter, a field or a binding. Each
    binder then OWNS its element when the whole is owned, and BORROWS it from the whole's
    owner when the whole is a borrow, so a change of that owner is refused while a binder
    lives.
    """
    whole = checker.borrow_state.get(stmt.name)
    borrowed = whole is not None and whole.is_borrowed_binding
    owner = whole.borrows_from if borrowed and whole is not None else None
    owner_state = checker.borrow_state.get(owner) if owner is not None else None
    shown = _element_spellings(stmt) if stmt.rebinds else {}
    for binder in destructure_binders(stmt.targets):
        state = BorrowState(name=binder.name, var_type=binder.element_type,
                            declared_at_span=binder.loc,
                            declared_branch_depth=checker.branch_depth)
        if binder.name in shown:
            state.written = WrittenLet(shown[binder.name])
        checker.borrow_state[binder.name] = state
        if not borrowed:
            continue
        state.is_borrowed_binding = True
        state.is_let_borrow = True
        state.bound_at_span = stmt.loc
        if owner is not None and owner_state is not None:
            state.borrows_from = owner
            owner_state.binding_borrows.append((binder.name, stmt.loc))
            checker._scope_binding_borrows[-1].append((owner, binder.name))


def _element_spellings(stmt: Let) -> dict[str, str]:
    """The hidden binders of `(a, b) := v`, each spelled as the element it takes (`v.0`).

    A diagnostic then names `p.0`, which the user can read, and never the hidden binder.
    The value has a spelling only when it is a place; a temporary needs none, because
    each binder owns its element.
    """
    source = expr_to_string(stmt.value)
    if "<expression>" in source:
        return {}
    shown: dict[str, str] = {}

    def spell(targets, prefix: str) -> None:
        for index, target in enumerate(targets):
            if target.nested is not None:
                spell(target.nested, f"{prefix}.{index}")
            elif target.name is not None:
                shown[target.name] = f"{prefix}.{index}"

    spell(stmt.targets or [], source)
    return shown


def _check_rebind(checker: 'BorrowChecker', stmt: Rebind) -> None:
    """`x := v` or `obj.field := v`: the target takes ownership of a new value."""
    target = stmt.target
    owner = borrow_owner(target)

    # No "rebind while borrowed" check here, deliberately: this runs BEFORE the value
    # walk, and moving it after would reject `x := f(peek x)`. CE2401 lives at the
    # consuming use instead.
    if isinstance(target, Name):
        state = checker.borrow_state.get(target.id)
        if state is not None:
            # THE gate, in the rebind position: a name that is a view of another value's
            # storage cannot be rebound, a name with storage of its own can. A `poke`
            # reference is the middle case and stays legal -- the store goes through the
            # pointer, which is what the mode is for.
            refused = reject_readonly_write(checker, target.id, stmt.loc,
                                            "rebind the name", rebind=True)
            # The store goes through the pointer, so it is a USE of the reference: after
            # a change of its owner it writes into storage the owner no longer holds.
            if not refused and isinstance(state.var_type, ReferenceType):
                reject_a_use_after_the_change(checker, target.id, target.loc)
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
        # An element rebind mutates in place too, and `borrow_owner` already walks an
        # index, so the same gate answers for all five read-only receiver kinds.
        reject_readonly_write(checker, owner, stmt.loc, "assign to an array element",
                              receiver=target.array)
        check_expr(checker, target)

    check_expr(checker, stmt.value)
    # Both rebind shapes take ownership, and replacing what the owner holds invalidates
    # every binding reading out of it (#242).
    check_owner_not_borrowed(checker, owner, stmt.loc, "assign", place=target)

    if isinstance(target, Name):
        consume(checker, stmt.value)
        # A rebind RE-INITIALIZES, so a previous move no longer holds. The value was
        # checked above, so `s := "{s}-x"` still reports the moved `s`.
        target_state = checker.borrow_state.get(target.id)
        if target_state is not None:
            reinitialize(target_state)
    elif isinstance(target, MemberAccess):
        consume(checker, stmt.value)
    elif isinstance(target, IndexAccess):
        consume(checker, stmt.value)
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
        if not terminates(arm_block, leaves_round=True):
            paths.append(snapshot_flow(checker))
    if stmt.else_block:
        restore_flow(checker, entry)
        with _branch(checker):
            check_block(checker, stmt.else_block)
        if not terminates(stmt.else_block, leaves_round=True):
            paths.append(snapshot_flow(checker))
    else:
        paths.append(entry)
    restore_flow(checker, FlowFacts.join(paths))


def _match_owns(checker: 'BorrowChecker', stmt: Match, scrutinee: Expr) -> bool:
    """May an arm TAKE a payload out of this scrutinee (ruling R11)?

    A TEMPORARY is owned by construction -- nothing else will ever free it, which is the
    same predicate `own_temporary` makes in the backend. A place expression belongs to
    its owner unless the match says `nom` and takes it.
    """
    if stmt.consumes_scrutinee:
        return True
    return source_provenance(checker, scrutinee) is Provenance.FRESH


def match_scrutinees(stmt: Match) -> list[Expr]:
    """The expressions a match reads: the scrutinee, or each element of a tuple literal.

    A tuple-literal scrutinee builds no tuple (ruling 3 of the tuple design): each
    element is matched in place, with the rules of a named scrutinee.
    """
    if isinstance(stmt.scrutinee, TupleLiteral):
        return list(stmt.scrutinee.elements)
    return [stmt.scrutinee]


def _arm_positions(checker: 'BorrowChecker', stmt: Match,
                   pattern: object) -> list[tuple[Expr, object, object]]:
    """(scrutinee, its type, the pattern item that reads it) for one arm."""
    if not isinstance(pattern, (Pattern, TuplePattern)):
        return []
    if not isinstance(stmt.scrutinee, TupleLiteral):
        return [(stmt.scrutinee, stmt.resolved_scrutinee_type, pattern)]
    if not isinstance(pattern, TuplePattern):
        return []
    types = checker.types.tuple_element_types(stmt.resolved_scrutinee_type)
    return [(element, types[index] if index < len(types) else None, item)
            for index, (element, item) in enumerate(
                zip(stmt.scrutinee.elements, pattern.elements, strict=False))]


def _check_match(checker: 'BorrowChecker', stmt: Match) -> None:
    """Match arms are EXCLUSIVE paths, so they take the same snapshot / restore / join."""
    scrutinees = match_scrutinees(stmt)
    for scrutinee in scrutinees:
        check_expr(checker, scrutinee)
    owns = {id(scrutinee): _match_owns(checker, stmt, scrutinee) for scrutinee in scrutinees}
    if stmt.consumes_scrutinee:
        # `match nom r:` consumes exactly as `f(nom r)` does: CE2411 when `r` is a
        # borrow, and CE2405 at every later mention of it. `match nom (a, b):` hands
        # over each element.
        for scrutinee in scrutinees:
            consume(checker, scrutinee)
    clear_borrows(checker)
    entry = snapshot_flow(checker)
    paths: list[FlowFacts] = []
    for arm in stmt.arms:
        restore_flow(checker, entry)
        # The scrutinee type gives each binding its var_type; the typecheck pass stamps it (CE0121
        # guards that). The scope closes BEFORE the path snapshot, so the join sees the
        # outer local's facts, never the binding's.
        with BindingScope(checker) as scope, _branch(checker):
            for scrutinee, scrutinee_type, item in _arm_positions(checker, stmt, arm.pattern):
                reject_partial_take(checker, item, scrutinee_type)
                register_pattern_bindings(checker, scope, item, scrutinee_type,
                                          scrutinee=scrutinee,
                                          owns_scrutinee=owns[id(scrutinee)])
            if isinstance(arm.body, Block):
                check_block(checker, arm.body)
            else:
                check_expr(checker, arm.body)
                clear_borrows(checker)
        if not terminates(arm.body, leaves_round=True):
            paths.append(snapshot_flow(checker))
    # A `match` is exhaustive (the typecheck pass enforces it), so unlike an `if` with no else there
    # is no fall-through path to add: some arm always runs.
    restore_flow(checker, FlowFacts.join(paths))


def _check_foreach(checker: 'BorrowChecker', stmt: Foreach) -> None:
    """The loop variable lives for the LOOP and no longer; what it is depends on the walk.

    Over a container the item BORROWS the element. Over a `next()` protocol iterator
    the item is the value `next()` answered, and the iteration OWNS it. The loop also
    takes the protocol iterator itself, as `f(nom it)` does: CE2411 for a borrow, and
    CE2405 at a later mention of a local (#1145).
    """
    check_expr(checker, stmt.iterable)
    if stmt.protocol_next is not None:
        consume(checker, stmt.iterable)
    clear_borrows(checker)
    # A value binding matches the backend's `register_cleanup=False`. A reference binding
    # (#300) and a value binding of an owning element (#919) freeze the container for the
    # loop. Both end with the scope, so an outer local the item shadows gets its state
    # back (#337).
    # The iterator over a container freezes it against a change that moves or frees its
    # storage, for the whole loop (#956). The item's freeze stays for an in-place change;
    # a change that hits both is the iterator's to report.
    span = stmt.item_name_span or stmt.loc
    with BindingScope(checker) as scope:
        iterator = (scope.bind_iterator(stmt.iterable)
                    if stmt.protocol_next is None else None)
        if stmt.item_borrow is not None:
            owner: Optional[Expr] = stmt.iterable
            # An item over an owned temporary has no named owner to conflict with.
            if not walks_a_temporary(checker, stmt.iterable) and reject_a_second_reference(
                    checker, stmt.iterable, stmt.item_borrow,
                    stmt.item_borrow_span or span):
                owner = None
            scope.bind_ref(stmt.item_name, stmt.item_type, stmt.item_borrow, span,
                           owner=owner, declared_at=stmt.item_borrow_span)
        elif stmt.protocol_next is not None:
            scope.bind_item(stmt.item_name, stmt.item_type, span)
        else:
            scope.bind_value(stmt.item_name, stmt.item_type, span)
            if not walks_a_temporary(checker, stmt.iterable):
                freeze_for_a_view(checker, scope, stmt.item_name, stmt.item_type, span,
                                  stmt.iterable, ScrutineeKind.BORROWED)
        if iterator is not None:
            checker.borrow_state[stmt.item_name].covered_by = iterator.name
        flow = check_loop_body(checker, stmt.body,
                               per_iteration=frozenset({stmt.item_name}),
                               view=iterator.name if iterator is not None else None)
        # Only a change that reaches the next round meets the iterator again (#993).
        change = (flow.back_edge.invalidation_of(iterator.name)
                  if iterator is not None else None)
        if change is not None:
            emit_change_under_iterator(checker, change, stmt.iterable, stmt.loc)


def _check_expand(checker: 'BorrowChecker', stmt: Expand) -> None:
    """An `expand` body of a check copy, checked once for every pack length (#1070, R6).

    The body runs zero or more times, as a `foreach` body does, so a move of an outer
    owned value in it is a move in a loop. The binder is one element of the value pack:
    a borrow of the caller's value, or an owned value when the template takes the pack
    with `nom`, as each copy declares the element parameter. Only a check copy holds an
    `expand` with an element type. A written template that no check copy covers was
    checked at its own build, and its `expand` body is not read here.
    """
    element_of = checker.pack_element_of
    if element_of is None:
        return
    owned = isinstance(stmt.iterable, Name) and stmt.iterable.id in checker.owned_packs
    state = BorrowState(name=stmt.var, var_type=element_of(stmt),
                        declared_at_span=stmt.var_span or stmt.loc,
                        declared_branch_depth=checker.branch_depth + 1)
    state.is_borrow_param = not owned
    with BindingScope(checker) as scope:
        scope.register(state)
        check_loop_body(checker, stmt.body, per_iteration=frozenset({stmt.var}),
                        exits_leave=True)


def check_loop_body(checker: 'BorrowChecker', body: Block,
                    per_iteration: frozenset[str] = frozenset(),
                    view: Optional[str] = None, exits_leave: bool = False) -> LoopFlow:
    """Borrow-check a loop body to a fixed point so the back edge is honoured.

    `per_iteration` names the bindings the loop creates anew on every pass -- the item --
    whose facts at the end of the body do not reach the next iteration. A `break` path
    does not reach the back edge; its facts join the loop's exit (#993).

    `view` is the binding that walks a container for the whole loop (a foreach
    iterator). When a move of that container reaches the back edge, the iterator reports
    it (CE2412), so the second round does not report that move again at a use (#995).

    `exits_leave` is for an `expand` body (#1070, R6): it repeats once per element, but
    it is no loop of its own, so a `break` or a `continue` in it goes to the ENCLOSING
    loop. The reporting round hands those paths to the enclosing frame.
    """
    enclosing = checker._loop_frames[-1] if exits_leave and checker._loop_frames else None
    entry = snapshot_flow(checker)
    prev_suppressed = checker.err.suppressed
    checker.err.suppressed = True
    first_back, _first_breaks = _check_round(checker, body, per_iteration,
                                             exits_leave=exits_leave)
    checker.err.suppressed = prev_suppressed
    fixed_point = entry if first_back is None else entry | first_back
    restore_flow(checker, fixed_point)
    reported = _move_reported_by_view(checker, view, entry, first_back)
    if reported is not None:
        reported.move_reported_by = view
    try:
        back, breaks = _check_round(checker, body, per_iteration,
                                    exits_leave=exits_leave, enclosing=enclosing)
    finally:
        if reported is not None:
            reported.move_reported_by = None
    flow = LoopFlow(entry=entry, fixed_point=fixed_point,
                    back_edge=fixed_point if back is None else fixed_point | back,
                    exit=fixed_point if breaks is None else fixed_point | breaks)
    restore_flow(checker, flow.exit)
    return flow


def _move_reported_by_view(checker: 'BorrowChecker', view: Optional[str],
                           entry: FlowFacts, first_back: FlowFacts | None
                           ) -> Optional[BorrowState]:
    """The owner whose move came round the back edge and invalidated `view`, or None."""
    if view is None or first_back is None:
        return None
    change = first_back.invalidation_of(view)
    if change is None:
        return None
    _span, (owner, _what) = change
    if owner not in first_back.moved or owner in entry.moved:
        return None
    return checker.borrow_state.get(owner)


def _check_round(checker: 'BorrowChecker', body: Block, per_iteration: frozenset[str],
                 exits_leave: bool = False, enclosing: Optional[LoopFrame] = None
                 ) -> tuple[FlowFacts | None, FlowFacts | None]:
    """Check one round of a loop body: the paths to the back edge, the `break` paths.

    Each is None when no path of that kind exists. `FlowFacts.join` of no path is not an
    identity, so a caller joins a None as nothing. With `exits_leave` the `break` and
    `continue` paths belong to the enclosing loop: they go to `enclosing` (none in a
    discovery round), and the round itself has only the path to its end.
    """
    frame = LoopFrame()
    checker._loop_frames.append(frame)
    try:
        with _branch(checker):
            check_block(checker, body)
    finally:
        checker._loop_frames.pop()
    if exits_leave:
        if enclosing is not None:
            enclosing.breaks.extend(p.without(per_iteration) for p in frame.breaks)
            enclosing.continues.extend(p.without(per_iteration) for p in frame.continues)
        frame = LoopFrame()
    back = list(frame.continues)
    if not terminates(body, leaves_round=True):
        back.append(snapshot_flow(checker))
    return _join_round(back, per_iteration), _join_round(frame.breaks, per_iteration)


def _join_round(paths: list[FlowFacts], per_iteration: frozenset[str]
                ) -> FlowFacts | None:
    return FlowFacts.join(paths).without(per_iteration) if paths else None
