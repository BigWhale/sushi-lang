"""SHIR to SLIR: the control-flow arms of the lowering (docs/design/ir.md 8.9, 8.10).

The arms of `if`, `while`, `break`, `continue` and `assert`, the lowering of `expect` as an
`assert`, the lowering of a condition, the statements of an arm with its binders, the join
of the paths of a statement with the drop state of each path, the short-circuit `and` and
`or`, and `??`. The arm of `match` is in `lower_match.py`. Each arm is a free function that
takes the `Lowering` of `lower.py` first; the tables of `lower.py` name them. This module
reads `lower.py` for its types only, so no import cycle exists at run time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Sequence, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.method_effects import MAYBE_FAMILY, RESULT_FAMILY
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin, BuiltinMethod, Conversion
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.loop_kind import While
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.drops import FlowState
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Arrival, Block, Lowering, ShortCircuit, Value


#: The binders that one path of an arm writes, and the block that writes them: None for the
#: block of the body.
Binds: TypeAlias = tuple[Optional["Block"], tuple[tuple[ShirLocalId, p.Place], ...]]


@dataclass(slots=True)
class LoopFrame:
    """The loop that the walk is in: its header and exit blocks, the count of the scopes
    outside its body, and the state of each `break` and of each path to the header."""
    header: BlockId
    exit: BlockId
    depth: int
    breaks: list[FlowState] = field(default_factory=list)
    continues: list[FlowState] = field(default_factory=list)


def arm(low: Lowering, block: Block, body: ShirBlock, binds: Sequence[Binds] = (),
        span: Optional[Span] = None) -> list[Arrival]:
    """The statements of `body` in a scope of their own, from `block` on, after the binders
    of a match arm. The answer is the edge into the join when the path reaches the end of
    `body`."""
    low.block = block
    low.placement.open_scope()
    _bind(low, block, binds, span or body.loc)
    for stmt in body.statements:
        low.statement(stmt)
    if not low.alive:
        low.placement.leave_scope()
        return []
    low.emit(low.placement.flush())
    low.emit(low.placement.close_scope(body.loc))
    return [(low.block, low.placement.state())]


def _bind(low: Lowering, body: Block, binds: Sequence[Binds], span: Span) -> None:
    """Each binder is a local of the scope of the arm. A path writes its binders in its own
    block, which goes to the body, or in the body block when it is the one path."""
    idents: dict[ShirLocalId, LocalId] = {}
    for block, writes in binds:
        statements: list[st.SlirStatement] = []
        for local, place in writes:
            if local not in idents:
                idents[local] = low.bind(local, span)[0]
                low.placement.declare(idents[local], False)
            statements += [st.StorageLive(idents[local], span),
                           st.Assign(p.Local(idents[local]), r.Use(p.Copy(place)), span)]
        if block is None:
            low.emit(statements)
        else:
            block.statements.extend(statements)
            block.terminator = tm.Goto(body.id, span)


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
    value = low.value(expr)
    local: Optional[LocalId] = None
    if isinstance(value, r.Use) and isinstance(value.operand, (p.Const, p.Copy)):
        operand = value.operand
    else:
        local = low.temporary(value, BuiltinType.BOOL, expr.loc)
        low.placement.detach(local)
        operand = p.Copy(p.Local(local))
    low.emit(low.placement.close_scope(expr.loc))
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
    join = low.new_block() if stmt.otherwise is None and local is not None else None
    low.jump(tm.SwitchInt(cond, ((0, other.id),), then.id, stmt.loc))
    targets(local, (then, other), stmt.cond.loc)
    among, at_branch = low.placement.in_scope(), low.placement.state()
    arrivals = arm(low, then, stmt.then)
    if stmt.otherwise is None:
        zero: Arrival = (None, at_branch) if join is None else (other, at_branch)
        meet(low, other if join is None else join, [*arrivals, zero], among, stmt.loc)
        return
    low.placement.restore(at_branch)
    arrivals += arm(low, other, stmt.otherwise)
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
    _trap_path(low, fail, next_, stmt.loc,
               lambda at: tm.Trap("RE2026", _assert_operands(low, stmt, at), stmt.loc))


def _assert_operands(low: Lowering, stmt: s.Assert, at: str) -> tuple[p.Operand, p.Operand]:
    """The position of an `assert` and its message."""
    if stmt.message is None:
        return p.Const(k.text(at)), p.Const(k.text(""))
    return p.Const(k.text(f"{at}: ")), low.operand(stmt.message)


def _trap_path(low: Lowering, fail: Block, next_: Block, span: Span,
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
    """A `while`: a header block that evaluates the condition and switches, the body,
    which goes back to the header, and the exit block (ir.md 8.9). When a condition
    local must end on the zero edge, that edge goes to a block of its own, which goes
    to the exit, because a `break` goes to the exit too."""
    source = stmt.source
    if not (isinstance(source, s.Cond)
            and isinstance(low.typeck.loop_kind_of(stmt.shir_id), While)):
        dispatch.stop(STAGE, stmt)
    header = low.new_block()
    low.jump(tm.Goto(header.id, stmt.loc))
    low.block = header
    among = low.placement.in_scope()
    for kept in among:
        if (stmt.shir_id, low.shir_of.get(kept)) in low.learned.unknown_at:
            low.placement.mark_unknown(kept)
    at_header = low.placement.state()
    cond, local = condition(low, source.cond)
    body = low.new_block()
    done = None if local is None else low.new_block()
    exit_ = low.new_block()
    low.jump(tm.SwitchInt(cond, ((0, (done or exit_).id),), body.id, stmt.loc))
    targets(local, (body,) if done is None else (body, done), source.cond.loc)
    after_cond = low.placement.state()
    frame = LoopFrame(header.id, exit_.id, low.placement.depth())
    low.loops.append(frame)
    for block, state in arm(low, body, stmt.body):
        if block is not None:
            block.terminator = tm.Goto(header.id, stmt.loc)
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
    low.jump(tm.Goto(frame.exit if breaks else frame.header, span))


def short_circuit(low: Lowering, place: p.Place, value: ShortCircuit) -> None:
    """`and` and `or` as two blocks (ir.md 8.9): the left operand goes into `place`, a
    switch on it skips the right block, and the right block writes `place` again. The
    temporaries of the right operand die in its block."""
    span = value.span
    low.write(place, r.Use(value.lhs), span)
    right, join = low.new_block(), low.new_block()
    skip, other = (join, right) if value.op == "and" else (right, join)
    low.jump(tm.SwitchInt(p.Copy(place), ((0, skip.id),), other.id, span))
    among, at_branch = low.placement.in_scope(), low.placement.state()
    low.block = right
    low.placement.open_statement()
    low.write(place, low.value(value.rhs), span)
    low.emit(low.placement.flush())
    low.emit(low.placement.close_scope(span))
    meet(low, join, [(None, at_branch), (low.block, low.placement.state())], among, span)


def try_(low: Lowering, node: e.Try) -> Value:
    """`??` (ir.md 8.9): the operand into a temporary, a switch on its discriminant with
    no other value, an `Err` block that builds the `Err` into `_0`, leaves every scope
    and returns, and the `Ok` block, where the payload moves into the place that takes
    the value; each store moves the temporary. A `??` that calls a declared conversion
    stops (`slir, conversion`), and a `??` over an owning wrapper that the body only reads
    stops (`slir, borrowed try`)."""
    facts = low.typeck.try_facts_of(node.shir_id)
    choice = low.typeck.conversion_of(node.shir_id)
    if isinstance(choice, Conversion):
        raise StageStop(STAGE, "conversion", node.loc)
    if facts is None or choice is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a ?? with no facts reached SLIR")
    ty = low.type_of(node.inner)
    value = low.value(node.inner)
    if low.owns(ty) and isinstance(value, r.Use) and isinstance(value.operand, p.Copy):
        raise StageStop(STAGE, "borrowed try", node.loc)
    (ok_tag, ok), (err_tag, err) = _arms(ty, facts.success_tag, node.loc)
    temp = low.temporary(value, ty, node.inner.loc)
    tag = low.new_local(BuiltinType.I32, None, None)
    low.emit([st.Assign(p.Local(tag), r.Discriminant(p.Local(temp)), node.loc)])
    ok_block, err_block = low.new_block(), low.new_block()
    targets = tuple(sorted([(ok_tag, ok_block.id), (err_tag, err_block.id)]))
    low.jump(tm.SwitchInt(p.Copy(p.Local(tag)), targets, None, node.loc))
    at_switch = low.placement.state()
    low.block = err_block
    payload = p.Move(p.Field(p.Downcast(p.Local(temp), err), 0))
    low.finish(r.Aggregate(r.AggVariant(facts.returns, err), (payload,)), node.loc)
    low.placement.restore(at_switch)
    low.block = ok_block
    return r.Use(p.Move(p.Field(p.Downcast(p.Local(temp), ok), 0)))


def _arms(ty: Type, success: int, span: Span) -> tuple[tuple[int, str], tuple[int, str]]:
    """The tag and the name of the success variant and of the error variant of a `Result`."""
    if not isinstance(ty, EnumType) or len(ty.variants) != 2:
        er.raise_internal_error("CE0000", span=span, detail="a ?? over no Result reached SLIR")
    return (success, ty.variants[success].name), (1 - success, ty.variants[1 - success].name)


#: The variant of a `Result` or a `Maybe` that `expect` answers the payload of.
_SUCCESS = {RESULT_FAMILY: "Ok", MAYBE_FAMILY: "Some"}


def lowers_as_assert(method: BuiltinMethod) -> bool:
    """`expect` of a `Result` or a `Maybe` is no call: it keeps its lazy message (ir.md 7.4)."""
    return method.name == "expect" and method.family in _SUCCESS


def expect(low: Lowering, node: e.Call, callee: Builtin) -> Value:
    """`expect(msg)` as an `assert` (ir.md 8.9): a switch on the tag of the receiver. The
    failing block evaluates the message and ends in the trap RE2029, which reads the position
    of the call and the message, and drops nothing. The next block reads the payload: a copy
    of a plain payload and a clone of an owning one, because the receiver is a read (ir.md
    8.3). A constant receiver goes into a temporary, because a `Discriminant` reads a place."""
    ty = low.type_of(callee.recv)
    receiver = low.operand(callee.recv)
    if isinstance(receiver, p.Const):
        receiver = p.Copy(p.Local(low.temporary(r.Use(receiver), ty, callee.recv.loc)))
    success = _SUCCESS[callee.method.family]
    tag, payload = variant_of(ty, success, node.loc)
    found = low.new_local(BuiltinType.I32, None, None)
    low.emit([st.Assign(p.Local(found), r.Discriminant(receiver.place), node.loc)])
    fail, next_ = low.new_block(), low.new_block()
    low.jump(tm.SwitchInt(p.Copy(p.Local(found)), ((tag, next_.id),), fail.id, node.loc))
    (message,) = node.args
    _trap_path(low, fail, next_, node.loc, lambda at: tm.Trap(
        "RE2029", (p.Const(k.text(at)), low.operand(message.value)), node.loc))
    value = p.Copy(p.Field(p.Downcast(receiver.place, success), 0))
    return r.Clone(value) if low.owns(payload[0]) else r.Use(value)
