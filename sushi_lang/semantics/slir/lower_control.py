"""SHIR to SLIR: the control-flow arms of the lowering (docs/design/ir.md 8.9, 8.10).

The arms of `if`, `match`, `while`, `break`, `continue` and `assert`, the lowering of
`expect` as an `assert`, the lowering of a condition,
the join of the paths of a statement with the drop state of each path, the short-circuit
`and` and `or`, and `??`. Each arm is a free function that takes the `_Lowering` of
`lower.py` first; the tables of `lower.py` name them. This module reads `lower.py` for its
types only, so no import cycle exists at run time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.integer_patterns import literal_value, pattern_interval
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import patterns as pt
from sushi_lang.semantics.method_effects import MAYBE_FAMILY, RESULT_FAMILY
from sushi_lang.semantics.shir.callee import Builtin, BuiltinMethod, Conversion, Intrinsic
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
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.stmts import Bare
from sushi_lang.semantics.shir.spelling import spell_pattern
from sushi_lang.semantics.type_predicates import is_integer_type
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type
from sushi_lang.internals.diagnostics import StageStop

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import _Arrival, _Block, _Lowering, _ShortCircuit, _Value


#: The binders that one path of an arm writes, and the block that writes them: None for the
#: block of the body.
_Binds: TypeAlias = tuple[Optional["_Block"], tuple[tuple[ShirLocalId, p.Place], ...]]


@dataclass(slots=True)
class LoopFrame:
    """The loop that the walk is in: its header and exit blocks, the count of the scopes
    outside its body, and the state of each `break` and of each path to the header."""
    header: BlockId
    exit: BlockId
    depth: int
    breaks: list[FlowState] = field(default_factory=list)
    continues: list[FlowState] = field(default_factory=list)


def arm(low: _Lowering, block: _Block, body: ShirBlock, binds: Sequence[_Binds] = (),
        span: Optional[Span] = None) -> list[_Arrival]:
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


def _bind(low: _Lowering, body: _Block, binds: Sequence[_Binds], span: Span) -> None:
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


def meet(low: _Lowering, join: _Block, arrivals: list[_Arrival], among: tuple[LocalId, ...],
         span: Span) -> None:
    """The paths `arrivals` meet at `join`, and lowering goes on from it."""
    for block, _ in arrivals:
        if block is not None:
            block.terminator = tm.Goto(join.id, span)
    low.block = join
    learn(low, low.placement.join([state for _, state in arrivals], among), span)


def learn(low: _Lowering, locals_: tuple[LocalId, ...], span: Span,
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


def condition(low: _Lowering, expr: e.ShirExpr) -> tuple[p.Operand, Optional[LocalId]]:
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


def targets(local: Optional[LocalId], blocks: tuple[_Block, ...], span: Span) -> None:
    """The storage of the condition local `local` ends at the top of each target block."""
    if local is not None:
        for block in blocks:
            block.statements.append(st.StorageDead(local, span))


def if_(low: _Lowering, stmt: s.If) -> None:
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
        zero: _Arrival = (None, at_branch) if join is None else (other, at_branch)
        meet(low, other if join is None else join, [*arrivals, zero], among, stmt.loc)
        return
    low.placement.restore(at_branch)
    arrivals += arm(low, other, stmt.otherwise)
    if arrivals:
        meet(low, low.new_block(), arrivals, among, stmt.loc)


def assert_(low: _Lowering, stmt: s.Assert) -> None:
    """`switchInt(c, [(0, fail)], otherwise: next)` (ir.md 8.9). The failing block evaluates
    the message in a scope of its own and ends in the trap RE2026, which reads the position
    of the `assert` and the message (ir.md 8.8) and drops nothing. A bare `assert` gives the
    empty message, and its position has no `: `. The next block goes on from the state
    before the failing block."""
    cond, local = condition(low, stmt.cond)
    fail, next_ = low.new_block(), low.new_block()
    low.jump(tm.SwitchInt(cond, ((0, fail.id),), next_.id, stmt.loc))
    targets(local, (fail, next_), stmt.cond.loc)
    at_branch = low.placement.state()
    low.block = fail
    low.placement.open_statement()
    position = f"{stmt.label}:{stmt.loc.line}:{stmt.loc.col}"
    message: p.Operand = p.Const(k.text(""))
    if stmt.message is not None:
        position, message = f"{position}: ", low.operand(stmt.message)
    low.block.terminator = tm.Trap("RE2026", (p.Const(k.text(position)), message), stmt.loc)
    low.placement.leave_scope()
    low.placement.restore(at_branch)
    low.block = next_


def match(low: _Lowering, stmt: s.Match) -> None:
    """A match on an integer, a string or an enum (ir.md 8.9): ONE switch when each path of
    each arm tests at most a literal of an integer or a variant at the scrutinee, else a
    chain of tests in source order. Each arm starts from the state after the scrutinee, and
    the arms that reach their end meet at the join. A match on a tuple stops (A5)."""
    facts = low.typeck.match_facts_of(stmt.shir_id)
    if facts is None or not (facts.integer is not None or facts.scrutinee == BuiltinType.STRING
                             or isinstance(facts.scrutinee, EnumType)):
        dispatch.stop(STAGE, stmt)
    enum = isinstance(facts.scrutinee, EnumType)
    scrutinee = _scrutinee(low, stmt.scrutinee, place=enum)
    low.emit(low.placement.flush())
    among, at_switch = low.placement.in_scope(), low.placement.state()
    arms = [_paths(low, case.pattern, scrutinee, facts.scrutinee) for case in stmt.arms]
    if all(len(path.tests) <= 1 and all(test.operand == scrutinee and _case(test) is not None
                                        for test in path.tests)
           for paths in arms for path in paths):
        arrivals = _one_switch(low, stmt, arms, scrutinee, enum, at_switch)
    else:
        arrivals = _chain(low, stmt, arms, at_switch)
    if arrivals:
        meet(low, low.new_block(), arrivals, among, stmt.loc)


def _scrutinee(low: _Lowering, expr: e.ShirExpr, *, place: bool) -> p.Operand:
    """The operand that each test reads. A place is read where it is, and so is a constant
    when no test needs a place. A new value, a constant of an enum, and a local that the
    match takes (`match nom r:`), go into a temporary of the match statement, which drops it
    at its end."""
    value: _Value
    if isinstance(expr, (e.LocalRef, e.Lit, e.ConstRef)):
        operand = low.operand(expr)
        if isinstance(operand, p.Copy) or (isinstance(operand, p.Const) and not place):
            return operand
        value = r.Use(operand)
    else:
        value = low.value(expr)
    return p.Copy(p.Local(low.temporary(value, low.type_of(expr), expr.loc)))


@dataclass(frozen=True, slots=True)
class _Test:
    """A test of a path: the pattern that it tests, the operand that it reads, and the type
    of that operand."""
    pattern: pt.Variant | pt.Literal | pt.IntRange
    operand: p.Operand
    ty: Type


@dataclass(frozen=True, slots=True)
class _Path:
    """One way that a pattern matches: its tests in order, and the binders that it writes,
    each with its place (ir.md 8.9)."""
    tests: tuple[_Test, ...] = ()
    binds: tuple[tuple[ShirLocalId, p.Place], ...] = ()


def _paths(low: _Lowering, pattern: pt.ShirPattern | pt.PatBinding, operand: p.Operand,
           ty: Type) -> list[_Path]:
    """The paths of `pattern` at `operand`: depth first and from left to right, one path for
    each alternative of an `Or`. A binder is bare and its payload is plain, because `borrow`
    stops on any other binder."""
    if isinstance(pattern, pt.PatBinding):
        if not isinstance(pattern.by, Bare) or low.owns(ty):
            er.raise_internal_error("CE0000", detail="a binder that borrow does not check "
                                                     "reached SLIR")
        return [_Path(binds=((pattern.local, _place(operand)),))]
    if isinstance(pattern, pt.Wildcard):
        return [_Path()]
    if isinstance(pattern, (pt.Literal, pt.IntRange)):
        return [_Path((_Test(pattern, operand, ty),))]
    if isinstance(pattern, pt.Or):
        return [path for alt in pattern.alternatives for path in _paths(low, alt, operand, ty)]
    if not isinstance(pattern, pt.Variant):
        dispatch.stop(STAGE, pattern)
    payload = _variant_of(ty, pattern.variant, pattern.loc)[1]
    paths = [_Path((_Test(pattern, operand, ty),))]
    for index, item in enumerate(pattern.items):
        field = p.Field(p.Downcast(_place(operand), pattern.variant), index)
        paths = [_Path(head.tests + tail.tests, head.binds + tail.binds) for head in paths
                 for tail in _paths(low, item, p.Copy(field), payload[index])]
    return paths


def _place(operand: p.Operand) -> p.Place:
    if isinstance(operand, p.Const):
        er.raise_internal_error("CE0000", detail="a place of a pattern reads a constant")
    return operand.place


def _variant_of(ty: Type, variant: str, span: Span) -> tuple[int, tuple[Type, ...]]:
    """The tag and the payload types of the variant `variant` of the enum `ty`."""
    info = ty.get_variant(variant) if isinstance(ty, EnumType) else None
    if info is None or not isinstance(ty, EnumType):
        er.raise_internal_error("CE0000", span=span,
                                detail=f"the variant '{variant}' of no enum reached SLIR")
    return ty.variants.index(info), tuple(info.associated_types)


def _case(test: _Test) -> Optional[int]:
    """The case of one switch that a test at the scrutinee gives: the tag of a variant, or
    the value of an integer literal at the type of the scrutinee. None for a test that one
    switch cannot hold (a range, a string)."""
    if isinstance(test.pattern, pt.Variant):
        return _variant_of(test.ty, test.pattern.variant, test.pattern.loc)[0]
    if isinstance(test.pattern, pt.Literal) and is_integer_type(test.ty):
        return literal_value(test.pattern.value, test.ty)
    return None


def _switch_case(test: _Test) -> int:
    case = _case(test)
    if case is None:
        er.raise_internal_error("CE0000", span=test.pattern.loc,
                                detail="a test that no switch case holds reached a switch")
    return case


def _bind_blocks(low: _Lowering, paths: list[_Path]) -> list[_Block]:
    """A block for each path of an arm when two or more paths write a binder: each one
    writes the binders of its path and goes to the body."""
    if len(paths) < 2 or not any(path.binds for path in paths):
        return []
    return [low.new_block() for _ in paths]


def _binds(paths: list[_Path], blocks: list[_Block]) -> list[_Binds]:
    if blocks:
        return list(zip(blocks, (path.binds for path in paths), strict=True))
    return [(None, paths[0].binds)]


def _one_switch(low: _Lowering, stmt: s.Match, arms: list[list[_Path]], scrutinee: p.Operand,
                enum: bool, at_switch: FlowState) -> list[_Arrival]:
    """`switchInt(v, [(case, arm)...], otherwise: _ arm)`: a case for each path, at the tag
    of its variant or at the value of its literal; `otherwise` is None when the arms cover
    the type. The switch of an enum reads the tag of the scrutinee, from a local with no
    storage marker."""
    if enum:
        tag = low.new_local(BuiltinType.I32, None, None)
        low.emit([st.Assign(p.Local(tag), r.Discriminant(_place(scrutinee)), stmt.loc)])
        scrutinee = p.Copy(p.Local(tag))
    entries = []
    for paths in arms:
        entries.append((_bind_blocks(low, paths), low.new_block()))
    cases: list[tuple[int, BlockId]] = []
    otherwise: Optional[BlockId] = None
    for paths, (binds, block) in zip(arms, entries, strict=True):
        if any(not path.tests for path in paths):
            otherwise = block.id
            continue
        cases += [(_switch_case(path.tests[0]), (binds[index] if binds else block).id)
                  for index, path in enumerate(paths)]
    low.jump(tm.SwitchInt(scrutinee, tuple(cases), otherwise, stmt.loc))
    arrivals: list[_Arrival] = []
    for case, paths, (binds, block) in zip(stmt.arms, arms, entries, strict=True):
        low.placement.restore(at_switch)
        arrivals += arm(low, block, case.body, _binds(paths, binds), case.pattern.loc)
    return arrivals


def _chain(low: _Lowering, stmt: s.Match, arms: list[list[_Path]],
           at_switch: FlowState) -> list[_Arrival]:
    """The tests of each path of each arm in source order. A failed test goes to the first
    test of the next path of its arm, else to the first test of the next arm; after the last
    arm, to the trap RE2023, which names that arm (ir.md 8.8)."""
    arrivals: list[_Arrival] = []
    entry = low.block
    for index, (case, paths) in enumerate(zip(stmt.arms, arms, strict=True)):
        catch = next((path for path in paths if not path.tests), None)
        if catch is not None:
            low.placement.restore(at_switch)
            return arrivals + arm(low, entry, case.body, [(None, catch.binds)],
                                  case.pattern.loc)
        counts = [[2 if isinstance(test.pattern, pt.IntRange) else 1 for test in path.tests]
                  for path in paths]
        steps = [entry, *(low.new_block() for _ in range(sum(map(sum, counts)) - 1))]
        binds = _bind_blocks(low, paths)
        body, missed = low.new_block(), low.new_block()
        start = 0
        for at, (path, sizes) in enumerate(zip(paths, counts, strict=True)):
            end = start + sum(sizes)
            failed = steps[end] if end < len(steps) else missed
            for number, (test, size) in enumerate(zip(path.tests, sizes, strict=True)):
                last = number == len(path.tests) - 1
                matched = (binds[at] if binds else body) if last else steps[start + size]
                _test(low, steps[start:start + size], test, matched.id, failed.id)
                start += size
        if index == len(stmt.arms) - 1:
            missed.terminator = tm.Trap(
                "RE2023", (p.Const(k.text(_spell(low, case.pattern))),), stmt.loc)
        low.placement.restore(at_switch)
        arrivals += arm(low, body, case.body, _binds(paths, binds), case.pattern.loc)
        entry = missed
    return arrivals


def _spell(low: _Lowering, pattern: pt.ShirPattern) -> str:
    """The arm as it is written, for the trap after the last arm."""
    return spell_pattern(pattern, low.decls.name_of, lambda local: low.shir_locals[local].name)


def _test(low: _Lowering, steps: list[_Block], test: _Test, matched: BlockId,
          missed: BlockId) -> None:
    """One test in `steps`: a switch on the tag of a variant, a switch on an integer literal,
    `>=` and `<=` for a range, a call of `eq` for a string. A test local is written in its
    block and read by the switch of that block alone, so it gets no storage marker."""
    pattern, ty, span = test.pattern, test.ty, test.pattern.loc
    if isinstance(pattern, pt.Variant):
        (step,) = steps
        tag = low.new_local(BuiltinType.I32, None, None)
        step.statements.append(st.Assign(p.Local(tag), r.Discriminant(_place(test.operand)),
                                          span))
        step.terminator = tm.SwitchInt(p.Copy(p.Local(tag)), ((_switch_case(test), matched),),
                                       missed, span)
        return
    if isinstance(pattern, pt.IntRange):
        low_bound, high_bound = pattern_interval(pattern, ty)
        _compare(low, steps[0], r.Binary(">=", test.operand, p.Const(k.Int(low_bound, ty))),
                 steps[1].id, missed, span)
        _compare(low, steps[1], r.Binary("<=", test.operand, p.Const(k.Int(high_bound, ty))),
                 matched, missed, span)
        return
    (step,) = steps
    if is_integer_type(ty):
        step.terminator = tm.SwitchInt(
            test.operand, ((literal_value(pattern.value, ty), matched),), missed, span)
        return
    if not isinstance(pattern.value, e.StrConst):
        er.raise_internal_error("CE0000", span=span,
                                detail="a string match with an integer arm reached SLIR")
    found = low.new_local(BuiltinType.BOOL, None, None)
    step.statements.append(st.Call(
        p.Local(found), st.Builtin(Intrinsic("eq"), (ty, ty)),
        ((test.operand, Convention.GUARANTEED),
         (p.Const(k.text(pattern.value.value)), Convention.GUARANTEED)), span))
    step.terminator = tm.SwitchInt(p.Copy(p.Local(found)), ((0, missed),), matched, span)


def _compare(low: _Lowering, step: _Block, test: r.Binary, matched: BlockId, missed: BlockId,
             span: Span) -> None:
    found = low.new_local(BuiltinType.BOOL, None, None)
    step.statements.append(st.Assign(p.Local(found), test, span))
    step.terminator = tm.SwitchInt(p.Copy(p.Local(found)), ((0, missed),), matched, span)


def loop(low: _Lowering, stmt: s.Loop) -> None:
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


def break_(low: _Lowering, stmt: s.Break) -> None:
    leave_round(low, stmt.loc, breaks=True)


def continue_(low: _Lowering, stmt: s.Continue) -> None:
    leave_round(low, stmt.loc, breaks=False)


def leave_round(low: _Lowering, span: Span, *, breaks: bool) -> None:
    """A `break` or a `continue`: the drops of each scope of the loop body that it
    leaves (rule 2), then the jump to the exit or to the header."""
    if not low.loops:
        er.raise_internal_error("CE0000", span=span,
                                detail="a jump out of no loop reached SLIR")
    frame = low.loops[-1]
    low.emit(low.placement.exit_to(frame.depth, span))
    (frame.breaks if breaks else frame.continues).append(low.placement.state())
    low.jump(tm.Goto(frame.exit if breaks else frame.header, span))


def short_circuit(low: _Lowering, place: p.Place, value: _ShortCircuit) -> None:
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


def try_(low: _Lowering, node: e.Try) -> _Value:
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


def expect(low: _Lowering, node: e.Call, callee: Builtin) -> _Value:
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
    tag, payload = _variant_of(ty, success, node.loc)
    found = low.new_local(BuiltinType.I32, None, None)
    low.emit([st.Assign(p.Local(found), r.Discriminant(receiver.place), node.loc)])
    fail, next_ = low.new_block(), low.new_block()
    low.jump(tm.SwitchInt(p.Copy(p.Local(found)), ((tag, next_.id),), fail.id, node.loc))
    at_branch = low.placement.state()
    low.block = fail
    low.placement.open_statement()
    (message,) = node.args
    position = f"{low.label}:{node.loc.line}:{node.loc.col}"
    low.block.terminator = tm.Trap(
        "RE2029", (p.Const(k.text(position)), low.operand(message.value)), node.loc)
    low.placement.leave_scope()
    low.placement.restore(at_branch)
    low.block = next_
    value = p.Copy(p.Field(p.Downcast(receiver.place, success), 0))
    return r.Clone(value) if low.owns(payload[0]) else r.Use(value)
