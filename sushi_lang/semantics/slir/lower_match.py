"""SHIR to SLIR: the arm of `match` (docs/design/ir.md 8.9).

A match on an integer, a string or an enum is ONE switch, or a chain of tests over the
paths of each pattern. A match on a plain tuple is always a chain: the paths of a tuple
pattern are the `Field` projections of its parts. The arm is a free function that takes the `Lowering` of `lower.py`
first, and the table of `lower.py` names it. The statements of an arm with its binders and
the join are the arms of `lower_control.py`. This module reads `lower.py` for its types
only, so no import cycle exists at run time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements
from sushi_lang.semantics.integer_patterns import literal_value, pattern_interval
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as pt
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Intrinsic
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.spelling import spell_pattern
from sushi_lang.semantics.shir.stmts import Bare
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.drops import FlowState
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.ids import BlockId
from sushi_lang.semantics.slir.lower_control import Binds, arm, meet, variant_of
from sushi_lang.semantics.type_predicates import is_integer_type
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Arrival, Block, Lowering
    from sushi_lang.semantics.slir.lower_values import Value


def match(low: Lowering, stmt: s.Match) -> None:
    """A match on an integer, a string, an enum or a plain tuple (ir.md 8.9): ONE switch
    when each path of each arm tests at most a literal of an integer or a variant at the
    scrutinee, else a chain of tests in source order. A match on a tuple is a chain. Each arm
    starts from the state after the scrutinee, and the arms that reach their end meet at the
    join. A tuple that owns stops (`Match owning`), because its drops are Tier B."""
    facts = low.typeck.match_facts_of(stmt.shir_id)
    if facts is None:
        dispatch.stop(STAGE, stmt)
    enum = isinstance(facts.scrutinee, EnumType)
    tuple_ = is_tuple_type(facts.scrutinee)
    if not (facts.integer is not None or facts.scrutinee == BuiltinType.STRING or enum
            or tuple_):
        dispatch.stop(STAGE, stmt)
    if tuple_ and low.owns(facts.scrutinee):
        dispatch.stop(STAGE, stmt, state="owning")
    scrutinee = _scrutinee(low, stmt.scrutinee, place=enum or tuple_)
    low.emit(low.placement.flush())
    among, at_switch = low.placement.in_scope(), low.placement.state()
    arms = [_paths(low, case.pattern, scrutinee, facts.scrutinee) for case in stmt.arms]
    if not tuple_ and all(len(path.tests) <= 1
                          and all(test.operand == scrutinee and _case(test) is not None
                                  for test in path.tests)
                          for paths in arms for path in paths):
        arrivals = _one_switch(low, stmt, arms, scrutinee, enum, at_switch)
    else:
        arrivals = _chain(low, stmt, arms, at_switch)
    if arrivals:
        meet(low, low.new_block(), arrivals, among, stmt.loc)


def _scrutinee(low: Lowering, expr: e.ShirExpr, *, place: bool) -> p.Operand:
    """The operand that each test reads. A place is read where it is, and so is a constant
    when no test needs a place. A new value, a constant of an enum, and a local that the
    match takes (`match nom r:`), go into a temporary of the match statement, which drops it
    at its end."""
    value: Value
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


def _paths(low: Lowering, pattern: pt.ShirPattern | pt.PatBinding, operand: p.Operand,
           ty: Type) -> list[_Path]:
    """The paths of `pattern` at `operand`: depth first and from left to right, one path for
    each alternative of an `Or`. The items of a variant are at the `Field` projections of
    its `Downcast`, the parts of a tuple at the `Field` projections of the tuple. A binder is
    bare and its payload is plain, because `borrow` stops on any other binder."""
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
    if isinstance(pattern, pt.Tuple):
        return _product(low, [_Path()], pattern.elements, _place(operand), tuple_elements(ty))
    if not isinstance(pattern, pt.Variant):
        dispatch.stop(STAGE, pattern)
    payload = variant_of(ty, pattern.variant, pattern.loc)[1]
    return _product(low, [_Path((_Test(pattern, operand, ty),))], pattern.items,
                    p.Downcast(_place(operand), pattern.variant), payload)


def _product(low: Lowering, paths: list[_Path], items: list[pt.ShirPattern | pt.PatBinding],
             base: p.Place, types: Sequence[Type]) -> list[_Path]:
    """Each path of `paths`, then the paths of each item at `Field(base, index)`, from left
    to right: one path for each choice of a path of each item."""
    if len(items) != len(types):
        er.raise_internal_error("CE0000", detail="a pattern with another count of parts than "
                                                 "its type reached SLIR")
    for index, item in enumerate(items):
        paths = [_Path(head.tests + tail.tests, head.binds + tail.binds) for head in paths
                 for tail in _paths(low, item, p.Copy(p.Field(base, index)), types[index])]
    return paths


def _place(operand: p.Operand) -> p.Place:
    if isinstance(operand, p.Const):
        er.raise_internal_error("CE0000", detail="a place of a pattern reads a constant")
    return operand.place



def _case(test: _Test) -> Optional[int]:
    """The case of one switch that a test at the scrutinee gives: the tag of a variant, or
    the value of an integer literal at the type of the scrutinee. None for a test that one
    switch cannot hold (a range, a string)."""
    if isinstance(test.pattern, pt.Variant):
        return variant_of(test.ty, test.pattern.variant, test.pattern.loc)[0]
    if isinstance(test.pattern, pt.Literal) and is_integer_type(test.ty):
        return literal_value(test.pattern.value, test.ty)
    return None


def _switch_case(test: _Test) -> int:
    case = _case(test)
    if case is None:
        er.raise_internal_error("CE0000", span=test.pattern.loc,
                                detail="a test that no switch case holds reached a switch")
    return case


def _bind_blocks(low: Lowering, paths: list[_Path]) -> list[Block]:
    """A block for each path of an arm when two or more paths write a binder: each one
    writes the binders of its path and goes to the body."""
    if len(paths) < 2 or not any(path.binds for path in paths):
        return []
    return [low.new_block() for _ in paths]


def _binds(paths: list[_Path], blocks: list[Block]) -> list[Binds]:
    if blocks:
        return list(zip(blocks, (path.binds for path in paths), strict=True))
    return [(None, paths[0].binds)]


def _one_switch(low: Lowering, stmt: s.Match, arms: list[list[_Path]], scrutinee: p.Operand,
                enum: bool, at_switch: FlowState) -> list[Arrival]:
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
    arrivals: list[Arrival] = []
    for case, paths, (binds, block) in zip(stmt.arms, arms, entries, strict=True):
        low.placement.restore(at_switch)
        arrivals += arm(low, block, case.body, _binds(paths, binds), case.pattern.loc)
    return arrivals


def _chain(low: Lowering, stmt: s.Match, arms: list[list[_Path]],
           at_switch: FlowState) -> list[Arrival]:
    """The tests of each path of each arm in source order. A failed test goes to the first
    test of the next path of its arm, else to the first test of the next arm; after the last
    arm, to the trap RE2023, which names that arm (ir.md 8.8)."""
    arrivals: list[Arrival] = []
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


def _spell(low: Lowering, pattern: pt.ShirPattern) -> str:
    """The arm as it is written, for the trap after the last arm."""
    return spell_pattern(pattern, low.decls.name_of, lambda local: low.shir_locals[local].name)


def _test(low: Lowering, steps: list[Block], test: _Test, matched: BlockId,
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


def _compare(low: Lowering, step: Block, test: r.Binary, matched: BlockId, missed: BlockId,
             span: Span) -> None:
    found = low.new_local(BuiltinType.BOOL, None, None)
    step.statements.append(st.Assign(p.Local(found), test, span))
    step.terminator = tm.SwitchInt(p.Copy(p.Local(found)), ((0, missed),), matched, span)
