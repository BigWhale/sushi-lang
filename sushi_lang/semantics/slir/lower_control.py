"""SHIR to SLIR: the control-flow arms of the lowering (docs/design/ir.md 8.9, 8.10).

The arms of `if`, `while`, a counted loop over a range, `break`, `continue` and `assert`,
the failing path of an `assert` and of an `expect`, the lowering of a condition, the
statements of an arm with its binders, the join of the paths of a statement with the drop
state of each path, the short-circuit `and` and `or`, and `??`. The arm of `match` is in
`lower_match.py`, and `expect` is in `lower_copies.py`. Each arm is a free function that takes the `Lowering` of `lower.py`
first; the tables of `lower.py` name them. This module reads `lower.py` for its types
only, so no import cycle exists at run time.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Callable, Optional, Sequence, TypeAlias, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ranges import plan_range
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Conversion
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.loop_kind import CountedRange, While
from sushi_lang.semantics.shir.nodes import ShirBlock, ShirNode
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import lower_views as views
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.drops import FlowState
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Arrival, Block, Lowering
    from sushi_lang.semantics.slir.lower_values import ShortCircuit, Value


#: The binders that one path of an arm writes, each with its value, and the block that
#: writes them: None for the block of the body.
Binds: TypeAlias = tuple[Optional["Block"], tuple[tuple[ShirLocalId, r.Rvalue], ...]]


@dataclass(slots=True)
class LoopFrame:
    """The loop that the walk is in: the block that a `continue` goes to (the header of a
    `while`, the step of a counted loop), the exit block, the count of the scopes outside
    its body, and the state of each `break` and of each path to the next round."""
    next: BlockId
    exit: BlockId
    depth: int
    breaks: list[FlowState] = field(default_factory=list)
    continues: list[FlowState] = field(default_factory=list)


def arm(low: Lowering, block: Block, body: ShirBlock, binds: Sequence[Binds] = (),
        span: Optional[Span] = None, branch: Optional[ShirNode] = None,
        drops: Sequence[LocalId] = ()) -> list[Arrival]:
    """The statements of `body` in a scope of their own, from `block` on, after the binders
    of a match arm and the end of each region that the arm of `branch` ends at its top. The
    answer is the edge into the join when the path reaches the end of `body`. At that end,
    each temporary of `drops` drops: another arm takes it, so every arm leaves it moved."""
    low.block = block
    low.placement.open_scope()
    _bind(low, block, binds, span or body.loc)
    if branch is not None:
        views.enter_arm(low, branch, body)
    for stmt in body.statements:
        low.statement(stmt)
    if not low.alive:
        low.placement.leave_scope()
        return []
    low.emit(low.placement.flush())
    low.emit(low.placement.close_scope(body.loc))
    for local in drops:
        low.emit(low.placement.drop_on_path(local, body.loc))
    return [(low.block, low.placement.state())]


def _bind(low: Lowering, body: Block, binds: Sequence[Binds], span: Span) -> None:
    """Each binder is a local of the scope of the arm, a bare binder of an owning payload a
    view local, a `peek` or `poke` binder an address local, and a `nom` binder an owning
    local written with the `Move` of its payload: the arm takes the variant whole, so the
    root moves one time for all of them. A path writes its binders in its own block, which
    goes to the body, or in the body block when it is the one path."""
    idents: dict[ShirLocalId, LocalId] = {}
    for block, writes in binds:
        statements: list[st.SlirStatement] = []
        taken: list[LocalId] = []
        for local, value in writes:
            if isinstance(value, r.Ref):
                statements += views.bind_address(low, local, value, span)
                continue
            if isinstance(value, r.Use) and isinstance(value.operand, p.Move):
                statements += _bind_taken(low, local, value, span)
                held = p.root_of(value.operand.place)
                if isinstance(held, p.Local) and held.id not in taken:
                    taken.append(held.id)
                continue
            if views.binds_a_view(low, local):
                statements += views.bind_view(low, local, value, span)
                continue
            if local not in idents:
                idents[local] = low.bind(local, span)[0]
                low.placement.declare(idents[local], False)
            statements += [st.StorageLive(idents[local], span),
                           st.Assign(p.Local(idents[local]), value, span)]
        for ident in taken:
            low.placement.move(ident, span)
        if block is None:
            low.emit(statements)
        else:
            block.statements.extend(statements)
            block.terminator = tm.Goto(body.id, span)


def _bind_taken(low: Lowering, local: ShirLocalId, value: r.Use,
                span: Span) -> list[st.SlirStatement]:
    """A `nom` binder: an owning local of the arm, with its flag after its write."""
    ident, ty = low.bind(local, span)
    owning = low.owns(ty)
    if owning:
        low.give_flag(ident)
    low.placement.declare(ident, owning)
    return [st.StorageLive(ident, span), st.Assign(p.Local(ident), value, span),
            *low.placement.flag_declared(ident, span)]


def meet(low: Lowering, join: Block, arrivals: list[Arrival], among: tuple[LocalId, ...],
         span: Span) -> None:
    """The paths `arrivals` meet at `join`, and lowering goes on from it."""
    for block, _ in arrivals:
        if block is not None:
            block.terminator = tm.Goto(join.id, span)
    low.block = join
    learn(low, low.placement.join([state for _, state in arrivals], among), span)


def learn(low: Lowering, locals_: tuple[LocalId, ...], span: Span,
          loop: Optional[ShirId] = None) -> None:
    """Record a local that needs a flag (rule 4), found at a join, and with `loop` that
    the header of the loop does not know its state. Such a local must be marked
    conditionally moved by `borrow` (rule 3). The walk goes on; its output is not used,
    and the body is lowered again with what it found."""
    for local in locals_:
        shir = low.shir_of.get(local)
        if shir is None:
            er.raise_internal_error(
                "CE0000", span=span,
                detail=f"two paths give the temporary _{local.index} two states")
        if not low.answers.ownership.is_conditionally_moved(shir):
            er.raise_internal_error(
                "CE0000", span=span,
                detail=f"two paths give _{local.index} two states, and borrow does not "
                       f"mark it conditionally moved")
        low.found_flags.add(shir)
        if loop is not None:
            low.found_unknown.add((loop, shir))


def condition(low: Lowering, expr: e.ShirExpr) -> tuple[p.Operand, Optional[LocalId]]:
    """The operand that a switch reads, and the local that holds it, if any. The
    temporaries of the condition die before the switch. A condition that is no local or
    constant goes into a local of its own: it is live before its first write, an exit
    inside the condition ends it, and the caller ends it at the top of each block that
    the switch goes to (L12)."""
    low.placement.open_statement()
    return _test(low, low.value(expr), expr.loc)


def _test(low: Lowering, value: Value, span: Span) -> tuple[p.Operand, Optional[LocalId]]:
    """The tail of `condition`, in the scope that the caller opened: `value` into a local
    of its own unless it is a constant or a read of a place that outlives the scope, then
    the end of the scope."""
    local: Optional[LocalId] = None
    if isinstance(value, r.Use) and (isinstance(value.operand, p.Const) or (
            isinstance(value.operand, p.Copy) and not any(
                low.placement.ends_at_close(read)
                for read in p.locals_read(value.operand.place)))):
        operand = value.operand
    else:
        local = low.temporary(value, BuiltinType.BOOL, span)
        low.placement.detach(local)
        operand = p.Copy(p.Local(local))
    low.emit(low.placement.close_scope(span))
    return operand, local


def targets(local: Optional[LocalId], blocks: tuple[Block, ...], span: Span) -> None:
    """The storage of the condition local `local` ends at the top of each target block."""
    if local is not None:
        for block in blocks:
            block.statements.append(st.StorageDead(local, span))


def if_(low: Lowering, stmt: s.If) -> None:
    """`switchInt(c, [(0, else)], otherwise: then)`; with no `else`, the zero edge goes to
    the join, or to a block of its own when a condition local must end there (ir.md
    8.9). The blocks: `then`, `else`, then the join (L5)."""
    cond, local = condition(low, stmt.cond)
    then, other = low.new_block(), low.new_block()
    edge = stmt.otherwise is None and (local is not None or views.ends_at_arm(low, stmt, None))
    join = low.new_block() if edge else None
    low.jump(tm.SwitchInt(cond, ((0, other.id),), then.id, stmt.loc))
    targets(local, (then, other), stmt.cond.loc)
    among, at_branch = low.placement.in_scope(), low.placement.state()
    arrivals = arm(low, then, stmt.then, branch=stmt)
    if stmt.otherwise is None:
        low.placement.restore(at_branch)
        low.block = other
        views.enter_arm(low, stmt, None)
        zero: Arrival = (None, at_branch) if join is None else (other, low.placement.state())
        meet(low, other if join is None else join, [*arrivals, zero], among, stmt.loc)
        return
    low.placement.restore(at_branch)
    arrivals += arm(low, other, stmt.otherwise, branch=stmt)
    if arrivals:
        meet(low, low.new_block(), arrivals, among, stmt.loc)


def assert_(low: Lowering, stmt: s.Assert) -> None:
    """`switchInt(c, [(0, fail)], otherwise: next)` (ir.md 8.9). The failing block ends in
    the trap RE2026, which reads the position of the `assert` and the message (ir.md 8.8). A
    bare `assert` gives the empty message, and its position has no `: `."""
    cond, local = condition(low, stmt.cond)
    fail, next_ = low.new_block(), low.new_block()
    low.jump(tm.SwitchInt(cond, ((0, fail.id),), next_.id, stmt.loc))
    targets(local, (fail, next_), stmt.cond.loc)
    trap_path(low, fail, next_, stmt.loc,
               lambda at: tm.Trap("RE2026", _assert_operands(low, stmt, at), stmt.loc))


def _assert_operands(low: Lowering, stmt: s.Assert, at: str) -> tuple[p.Operand, p.Operand]:
    """The position of an `assert` and its message."""
    if stmt.message is None:
        return p.Const(k.text(at)), p.Const(k.text(""))
    return p.Const(k.text(f"{at}: ")), low.operand(stmt.message)


def trap_path(low: Lowering, fail: Block, next_: Block, span: Span,
               trap: Callable[[str], tm.Trap]) -> None:
    """The failing block of an `assert` or an `expect` (ir.md 8.9): it makes the trap from
    the position `file:line:col` (the file of the body, as a diagnostic names it) and
    evaluates its operands in a scope of its own, and the trap drops nothing. The next block
    goes on from the state before the failing block."""
    at_branch = low.placement.state()
    low.block = fail
    low.placement.open_statement()
    terminator = trap(f"{low.label}:{span.line}:{span.col}")
    low.block.terminator = terminator
    low.placement.leave_scope()
    low.placement.restore(at_branch)
    low.block = next_


def variant_of(ty: Type, variant: str, span: Span) -> tuple[int, tuple[Type, ...]]:
    """The tag and the payload types of the variant `variant` of the enum `ty`."""
    info = ty.get_variant(variant) if isinstance(ty, EnumType) else None
    tag = ty.get_variant_index(variant) if isinstance(ty, EnumType) else None
    if info is None or tag is None:
        er.raise_internal_error("CE0000", span=span,
                                detail=f"the variant '{variant}' of no enum reached SLIR")
    return tag, tuple(info.associated_types)


def loop(low: Lowering, stmt: s.Loop) -> None:
    """A `while` or a counted loop (ir.md 8.9), by the kind that typecheck gave it."""
    source = stmt.source
    kind = low.typeck.loop_kind_of(stmt.shir_id)
    if isinstance(source, s.Cond) and isinstance(kind, While):
        _while(low, stmt, source)
    elif (isinstance(source, s.Items) and isinstance(kind, CountedRange)
          and isinstance(source.iterable, e.Range)):
        _counted(low, stmt, source, source.iterable)
    else:
        dispatch.stop(STAGE, stmt)


def _while(low: Lowering, stmt: s.Loop, source: s.Cond) -> None:
    """A header block that evaluates the condition and switches, the body, which goes back
    to the header, and the exit block. When a condition local must end on the zero edge,
    that edge goes to a block of its own, which goes to the exit, because a `break` goes to
    the exit too."""
    header = _enter_header(low, stmt)
    among, at_header = _header_state(low, stmt)
    cond, local = condition(low, source.cond)
    body = low.new_block()
    done = None if local is None else low.new_block()
    exit_ = low.new_block()
    _rounds(low, stmt, (cond, local, source.cond.loc), body, header, done, exit_,
            (among, at_header), ())


def _counted(low: Lowering, stmt: s.Loop, source: s.Items, range_: e.Range) -> None:
    """The bounds, the count and the counter `k` at 0 in the scope of the loop statement,
    then a header that compares `k` with the count, the body, which binds the item, a step
    block that adds one to `k` and goes to the header, and the exit block. A `continue`
    goes to the step. The blocks are made in the order header, body, step, the zero block
    (see `_while`), exit."""
    span = stmt.loc
    count, item = _range_formula(low, range_)
    counter = low.temporary(r.Use(i32(0)), BuiltinType.I32, span)
    header = _enter_header(low, stmt)
    among, at_header = _header_state(low, stmt)
    low.placement.open_statement()
    cond, local = _test(low, r.Binary("<", p.Copy(p.Local(counter)), count), span)
    body, step = low.new_block(), low.new_block()
    done = None if local is None else low.new_block()
    exit_ = low.new_block()
    binds: tuple[Binds, ...] = () if isinstance(source.binder, s.Discard) else (
        (None, ((source.binder, item(p.Copy(p.Local(counter)))),)),)
    _rounds(low, stmt, (cond, local, span), body, step, done, exit_, (among, at_header),
            binds)
    step.statements.append(st.Assign(
        p.Local(counter), r.Binary("+", p.Copy(p.Local(counter)), i32(1)), span))
    step.terminator = tm.Goto(header.id, span)


def _range_formula(low: Lowering, range_: e.Range
                   ) -> tuple[p.Operand, Callable[[p.Operand], r.Rvalue]]:
    """The count of a range and the item at the counter (`semantics/ranges.py`): each bound
    is evaluated once, the start first, then `range_operands`. The item is `first + k`, or
    `first - k` with `.rev()`."""
    start, end = bound(low, range_.start), bound(low, range_.end)
    first, count = range_operands(low, range_, start, end)
    if range_.reverse:
        return count, lambda at: r.Binary("-", first, at)
    return count, lambda at: r.Binary("+", first, at)


def range_operands(low: Lowering, range_: e.Range, start: p.Operand,
                   end: p.Operand) -> tuple[p.Operand, p.Operand]:
    """The first value and the count of a range with the bounds `start` and `end`
    (`semantics/ranges.py`), for a counted loop and for a run of an array. Two constant bounds
    give the first value and the count of `plan_range`. Else the count is `end - start`,
    plus one when inclusive, and a count below one walks nothing; the first value is
    `start`, or with `.rev()` the last value: `end`, less one when exclusive."""
    low_value, high_value = int_of(start), int_of(end)
    if low_value is not None and high_value is not None:
        plan = plan_range(low_value, high_value, range_.inclusive, range_.reverse, range_.loc)
        return i32(plan.first), i32(plan.count)
    span = range_.loc
    count = low.temporary(r.Binary("-", end, start), BuiltinType.I32, span)
    if range_.inclusive:
        low.emit([st.Assign(p.Local(count), r.Binary("+", p.Copy(p.Local(count)), i32(1)),
                            span)])
    first = start
    if range_.reverse:
        first = end if range_.inclusive else p.Copy(p.Local(
            low.temporary(r.Binary("-", end, i32(1)), BuiltinType.I32, span)))
    return first, p.Copy(p.Local(count))


def bound(low: Lowering, expr: e.ShirExpr) -> p.Operand:
    """An `i32` as a constant, or in a local of the statement that later code cannot
    change: a bound of a counted loop, an index, a bound of a slice."""
    value = low.value(expr)
    if isinstance(value, r.Use) and isinstance(value.operand, p.Const):
        return value.operand
    return p.Copy(p.Local(low.temporary(value, BuiltinType.I32, expr.loc)))


def i32(value: int) -> p.Const:
    """The operand of the `i32` constant `value`."""
    return p.Const(k.Int(value, BuiltinType.I32))


def int_of(operand: p.Operand) -> int | None:
    """The value of an integer constant operand, or None for any other operand."""
    if isinstance(operand, p.Const) and isinstance(operand.constant, k.Int):
        return operand.constant.value
    return None


def _enter_header(low: Lowering, stmt: s.Loop) -> Block:
    header = low.new_block()
    low.jump(tm.Goto(header.id, stmt.loc))
    low.block = header
    return header


def _header_state(low: Lowering, stmt: s.Loop) -> tuple[tuple[LocalId, ...], FlowState]:
    """The locals in scope at the header, and their state there: a local whose state a
    back edge changes is unknown at the header."""
    among = low.placement.in_scope()
    for kept in among:
        if (stmt.shir_id, low.shir_of.get(kept)) in low.learned.unknown_at:
            low.placement.mark_unknown(kept)
    return among, low.placement.state()


def _rounds(low: Lowering, stmt: s.Loop,
            test: tuple[p.Operand, Optional[LocalId], Span], body: Block, next_: Block,
            done: Optional[Block], exit_: Block,
            header: tuple[tuple[LocalId, ...], FlowState], binds: Sequence[Binds]) -> None:
    """The switch of the header, the body with its binders, which goes to `next_`, and the
    exit, where the zero edge and each `break` meet."""
    cond, local, span = test
    among, at_header = header
    low.jump(tm.SwitchInt(cond, ((0, (done or exit_).id),), body.id, stmt.loc))
    targets(local, (body,) if done is None else (body, done), span)
    after_cond = low.placement.state()
    frame = LoopFrame(next_.id, exit_.id, low.placement.depth())
    low.loops.append(frame)
    for block, state in arm(low, body, stmt.body, binds):
        if block is not None:
            block.terminator = tm.Goto(next_.id, stmt.loc)
        frame.continues.append(state)
    low.loops.pop()
    learn(low, at_header.changed_by(frame.continues, among), stmt.loc, stmt.shir_id)
    meet(low, exit_, [(done, after_cond), *((None, state) for state in frame.breaks)],
         among, stmt.loc)


def break_(low: Lowering, stmt: s.Break) -> None:
    leave_round(low, stmt.loc, breaks=True)


def continue_(low: Lowering, stmt: s.Continue) -> None:
    leave_round(low, stmt.loc, breaks=False)


def leave_round(low: Lowering, span: Span, *, breaks: bool) -> None:
    """A `break` or a `continue`: the drops of each scope of the loop body that it
    leaves (rule 2), then the jump to the exit or to the header."""
    if not low.loops:
        er.raise_internal_error("CE0000", span=span,
                                detail="a jump out of no loop reached SLIR")
    frame = low.loops[-1]
    low.emit(low.placement.exit_to(frame.depth, span))
    (frame.breaks if breaks else frame.continues).append(low.placement.state())
    low.jump(tm.Goto(frame.exit if breaks else frame.next, span))


def short_circuit(low: Lowering, place: p.Place, value: ShortCircuit) -> None:
    """`and` and `or` as two blocks (ir.md 8.9): the left operand goes into `place`, a
    switch on it skips the right block, and the right block writes `place` again. The
    temporaries of the right operand die in its block."""
    span = value.span
    low.write(place, r.Use(value.lhs), span)
    right, join = low.new_block(), low.new_block()
    skip, other = (join, right) if value.op == "and" else (right, join)
    low.jump(tm.SwitchInt(p.Copy(place), ((0, skip.id),), other.id, span))
    among, at_branch, switch = low.placement.in_scope(), low.placement.state(), low.block
    low.block = right
    low.placement.open_statement()
    low.write(place, low.value(value.rhs), span)
    low.emit(low.placement.flush())
    low.emit(low.placement.close_scope(span))
    meet(low, join, [_skipped(low, switch, join, at_branch, span),
                     (low.block, low.placement.state())], among, span)


def _skipped(low: Lowering, switch: Block, join: Block, state: FlowState,
             span: Span) -> Arrival:
    """The edge that skips the right operand. A region that the right operand ended before
    a change of its owner (ir.md 8.6) ends on this edge too, in a block of its own."""
    ended = [region for region in state.regions if region not in low.placement.open_regions()]
    if not ended:
        return (None, state)
    bridge = low.new_block()
    bridge.statements += [st.EndBorrow(ident, span) for region in reversed(ended)
                          for ident in region.ids]
    bridge.terminator = tm.Goto(join.id, span)
    test = cast(tm.SwitchInt, switch.terminator)
    switch.terminator = replace(test, targets=tuple(
        (case, bridge.id if target == join.id else target) for case, target in test.targets),
        otherwise=bridge.id if test.otherwise == join.id else test.otherwise)
    return (bridge, replace(state, regions=low.placement.open_regions()))


def try_(low: Lowering, node: e.Try) -> Value:
    """`??` (ir.md 8.9): the operand into a temporary, a switch on its discriminant with
    no other value, an `Err` block that builds the `Err` into `_0`, leaves every scope
    and returns, and the `Ok` block, where the payload moves into the place that takes
    the value; each store moves the temporary. A `??` that calls a declared conversion
    stops (`slir, conversion`). A `??` over a borrowed owning wrapper (a `reject` row at its
    operand) takes nothing: it switches on the wrapper where it is, the `Err` block copies the
    error, which is plain, and the payload is a view of the wrapper. A `??` over
    `m.or_err(nom e)` with a borrowed `Maybe` (a `reject` row at the receiver) is ONE shape
    with no call and no `Result` value: it switches on the `Maybe` where it is, the `None`
    block builds `Err(e)` with the move of `e`, and the payload is a view of the `Maybe`
    (ir.md 8.9)."""
    facts = low.typeck.try_facts_of(node.shir_id)
    choice = low.typeck.conversion_of(node.shir_id)
    if isinstance(choice, Conversion):
        raise StageStop(STAGE, "conversion", node.loc)
    if facts is None or choice is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a ?? with no facts reached SLIR")
    ty = low.type_of(node.inner)
    (ok_tag, ok), (err_tag, err) = _arms(ty, facts.success_tag, node.loc)
    error_variant = err
    through = views.read_through(low, node.inner)
    if through is not None:
        wrapper, error = through
        (ok_tag, ok), (err_tag, _none) = _maybe_arms(low.type_of(views.receiver_of(
            low, node.inner)), node.loc)
        borrowed = True
    else:
        wrapper, borrowed = views.wrapper(low, node.inner)
        error = (p.Copy if borrowed else p.Move)(p.Field(p.Downcast(wrapper, err), 0))
    take = p.Copy if borrowed else p.Move
    tag = low.new_local(BuiltinType.I32, None, None)
    low.emit([st.Assign(p.Local(tag), r.Discriminant(wrapper), node.loc)])
    ok_block, err_block = low.new_block(), low.new_block()
    targets = tuple(sorted([(ok_tag, ok_block.id), (err_tag, err_block.id)]))
    low.jump(tm.SwitchInt(p.Copy(p.Local(tag)), targets, None, node.loc))
    at_switch = low.placement.state()
    low.block = err_block
    low.finish(r.Aggregate(r.AggVariant(facts.returns, error_variant), (error,)), node.loc)
    low.placement.restore(at_switch)
    low.block = ok_block
    value = r.Use(take(p.Field(p.Downcast(wrapper, ok), 0)))
    return views.view_of(value, value.operand) if borrowed else value


def _maybe_arms(ty: Type, span: Span) -> tuple[tuple[int, str], tuple[int, str]]:
    """The tag and the name of the `Some` variant and of the `None` variant of a `Maybe`."""
    maybe = deref_type(ty)
    some = maybe.get_variant_index("Some") if isinstance(maybe, EnumType) else None
    if not isinstance(maybe, EnumType) or some is None or len(maybe.variants) != 2:
        er.raise_internal_error("CE0000", span=span,
                                detail="a read-through or_err over no Maybe reached SLIR")
    return (some, "Some"), (1 - some, maybe.variants[1 - some].name)


def _arms(ty: Type, success: int, span: Span) -> tuple[tuple[int, str], tuple[int, str]]:
    """The tag and the name of the success variant and of the error variant of a `Result`."""
    if not isinstance(ty, EnumType) or len(ty.variants) != 2:
        er.raise_internal_error("CE0000", span=span, detail="a ?? over no Result reached SLIR")
    return (success, ty.variants[success].name), (1 - success, ty.variants[1 - success].name)

