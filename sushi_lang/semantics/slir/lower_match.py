"""SHIR to SLIR: the arm of `match` (docs/design/ir.md 8.9).

A match on an integer, a string or an enum is ONE switch, or a chain of tests over the
paths of each pattern. A match on a plain tuple is always a chain: the paths of a tuple
pattern are the `Field` projections of its parts. The arm is a free function that takes
the `Lowering` of `lower.py` first, and the table of `lower.py` names it. The statements
of an arm with its binders and the join are the arms of `lower_control.py`. This module
reads `lower.py` for its types only, so no import cycle exists at run time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.own import own_payload_type
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
from sushi_lang.semantics.shir.stmts import Bare, Ref
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.drops import FlowState
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.ids import BlockId, LocalId
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
    join. A bare binder of an owning payload is a view local (`lower_views.bind_view`), and
    a `nom` binder of one moves it out of a temporary of the scrutinee: each arm that takes
    nothing out of that temporary drops it at its end."""
    facts = low.typeck.match_facts_of(stmt.shir_id)
    if facts is None:
        dispatch.stop(STAGE, stmt)
    enum = isinstance(facts.scrutinee, EnumType)
    tuple_ = is_tuple_type(facts.scrutinee)
    if not (facts.integer is not None or facts.scrutinee == BuiltinType.STRING or enum
            or tuple_):
        dispatch.stop(STAGE, stmt)
    scrutinee: p.Operand | tuple[p.Operand, ...]
    if tuple_ and isinstance(stmt.scrutinee, e.TupleInit) and low.owns(facts.scrutinee):
        scrutinee = tuple(_scrutinee(low, part, place=True) for part in stmt.scrutinee.parts)
    else:
        scrutinee = _scrutinee(low, stmt.scrutinee, place=enum or tuple_)
    low.emit(low.placement.flush())
    among, at_switch = low.placement.in_scope(), low.placement.state()
    arms = [_paths(low, case.pattern, scrutinee, facts.scrutinee) for case in stmt.arms]
    taken = [_taken(paths) for paths in arms]
    drops = [tuple(sorted(set().union(*taken) - own, key=lambda local: local.index))
             for own in taken]
    if not tuple_ and not isinstance(scrutinee, tuple) and all(
            len(path.tests) <= 1 and all(test.operand == scrutinee and _case(test) is not None
                                         for test in path.tests)
            for paths in arms for path in paths):
        arrivals = _one_switch(low, stmt, arms, scrutinee, enum, at_switch, drops)
    else:
        arrivals = _chain(low, stmt, arms, at_switch, drops)
    if arrivals:
        meet(low, low.new_block(), arrivals, among, stmt.loc)


def _taken(paths: list[_Path]) -> set[LocalId]:
    """The temporaries of the scrutinee that an arm takes: the root of each `nom` binder
    of an owning payload (ir.md 8.9)."""
    roots = {p.root_of(value.operand.place) for path in paths for _, value in path.binds
             if isinstance(value, r.Use) and isinstance(value.operand, p.Move)}
    return {root.id for root in roots if isinstance(root, p.Local)}


def _scrutinee(low: Lowering, expr: e.ShirExpr, *, place: bool) -> p.Operand:
    """The operand that each test reads. A place (a local, a field, an element, a part of
    a `var` when a test needs a place) is read where it is, so a `poke` binder writes it,
    and so is a constant when no test needs a place. A new value, a constant of
    an enum, and a local that the match takes (`match nom r:`), go into a temporary of the
    match statement, which drops it at its end."""
    value: Value
    if isinstance(expr, (e.LocalRef, e.Field, e.Index, e.Lit, e.ConstRef)):
        operand = low.operand(expr, in_place=place)
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
    each with the copy of its place (ir.md 8.9)."""
    tests: tuple[_Test, ...] = ()
    binds: tuple[tuple[ShirLocalId, r.Rvalue], ...] = ()


def _paths(low: Lowering, pattern: pt.ShirPattern | pt.PatBinding,
           operand: p.Operand | tuple[p.Operand, ...], ty: Type) -> list[_Path]:
    """The paths of `pattern` at `operand`: depth first and from left to right, one path for
    each alternative of an `Or`. The items of a variant are at the `Field` projections of
    its `Downcast`, the parts of a tuple at the `Field` projections of the tuple, or at the
    operand of each part of a tuple literal that owns, which builds no tuple. The pointee of
    an `Own(...)` is at the `Deref` of the pointer field of the `Own@(T)`. A bare binder
    copies its place, a `nom` binder of an owning payload moves it, and a `peek` or `poke`
    binder writes the `Ref` of it."""
    if isinstance(operand, tuple):
        if isinstance(pattern, pt.Tuple):
            return _product(low, [_Path()], pattern.elements, operand, tuple_elements(ty))
        if isinstance(pattern, pt.Or):
            return [path for alt in pattern.alternatives
                    for path in _paths(low, alt, operand, ty)]
        if isinstance(pattern, pt.Wildcard):
            return [_Path()]
        er.raise_internal_error("CE0000", detail="a pattern of a tuple literal is no tuple")
    if isinstance(pattern, pt.PatBinding):
        if isinstance(pattern.by, Ref):
            return [_Path(binds=((pattern.local, r.Ref(_place(operand), pattern.by.mode)),))]
        if isinstance(pattern.by, pt.Nom) and low.owns(ty):
            return [_Path(binds=((pattern.local, r.Use(p.Move(_place(operand)))),))]
        if not isinstance(pattern.by, Bare):
            er.raise_internal_error("CE0000", detail="a binder that borrow does not check "
                                                     "reached SLIR")
        return [_Path(binds=((pattern.local, r.Use(p.Copy(_place(operand)))),))]
    if isinstance(pattern, pt.Own):
        pointee = own_payload_type(ty)
        if pointee is None:
            er.raise_internal_error("CE0000", detail="an Own pattern of a value that is no "
                                                     "Own reached SLIR")
        return _paths(low, pattern.inner, p.Copy(p.Deref(p.Field(_place(operand), 0))),
                      pointee)
    if isinstance(pattern, pt.Wildcard):
        return [_Path()]
    if isinstance(pattern, (pt.Literal, pt.IntRange)):
        return [_Path((_Test(pattern, operand, ty),))]
    if isinstance(pattern, pt.Or):
        return [path for alt in pattern.alternatives for path in _paths(low, alt, operand, ty)]
    if isinstance(pattern, pt.Tuple):
        return _product(low, [_Path()], pattern.elements, _fields(_place(operand), ty),
                        tuple_elements(ty))
    if not isinstance(pattern, pt.Variant):
        dispatch.stop(STAGE, pattern)
    payload = variant_of(ty, pattern.variant, pattern.loc)[1]
    base = p.Downcast(_place(operand), pattern.variant)
    return _product(low, [_Path((_Test(pattern, operand, ty),))], pattern.items,
                    tuple(p.Copy(p.Field(base, index)) for index in range(len(payload))),
                    payload)


def _fields(base: p.Place, ty: Type) -> tuple[p.Operand, ...]:
    return tuple(p.Copy(p.Field(base, index)) for index in range(len(tuple_elements(ty))))


def _product(low: Lowering, paths: list[_Path], items: list[pt.ShirPattern | pt.PatBinding],
             operands: Sequence[p.Operand], types: Sequence[Type]) -> list[_Path]:
    """Each path of `paths`, then the paths of each item at its operand, from left to
    right: one path for each choice of a path of each item."""
    if not len(items) == len(operands) == len(types):
        er.raise_internal_error("CE0000", detail="a pattern with another count of parts than "
                                                 "its type reached SLIR")
    for item, operand, ty in zip(items, operands, types, strict=True):
        paths = [_Path(head.tests + tail.tests, head.binds + tail.binds) for head in paths
                 for tail in _paths(low, item, operand, ty)]
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


def _bind_blocks(low: Lowering, stmt: s.Match, paths: list[_Path]) -> list[Block]:
    """A block for each path of an arm when two or more paths write a binder: each one
    writes the binders of its path and goes to the body. A view local and an address local
    have one write each, so two paths that write a binder of an owning payload or a `peek`
    or `poke` binder stop (`Match owning alternatives`)."""
    if len(paths) < 2 or not any(path.binds for path in paths):
        return []
    if any(isinstance(value, r.Ref) or low.owns(low.typeck.local_type(local)
                                                 or BuiltinType.BLANK)
           for path in paths for local, value in path.binds):
        dispatch.stop(STAGE, stmt, state="owning alternatives")
    return [low.new_block() for _ in paths]


def _binds(paths: list[_Path], blocks: list[Block]) -> list[Binds]:
    if blocks:
        return list(zip(blocks, (path.binds for path in paths), strict=True))
    return [(None, paths[0].binds)]


def _one_switch(low: Lowering, stmt: s.Match, arms: list[list[_Path]], scrutinee: p.Operand,
                enum: bool, at_switch: FlowState,
                drops: list[tuple[LocalId, ...]]) -> list[Arrival]:
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
        entries.append((_bind_blocks(low, stmt, paths), low.new_block()))
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
    for case, paths, (binds, block), dropped in zip(stmt.arms, arms, entries, drops,
                                                    strict=True):
        low.placement.restore(at_switch)
        arrivals += arm(low, block, case.body, _binds(paths, binds), case.pattern.loc,
                        branch=stmt, drops=dropped)
    return arrivals


def _chain(low: Lowering, stmt: s.Match, arms: list[list[_Path]],
           at_switch: FlowState, drops: list[tuple[LocalId, ...]]) -> list[Arrival]:
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
                                  case.pattern.loc, branch=stmt, drops=drops[index])
        counts = [[2 if isinstance(test.pattern, pt.IntRange) else 1 for test in path.tests]
                  for path in paths]
        steps = [entry, *(low.new_block() for _ in range(sum(map(sum, counts)) - 1))]
        binds = _bind_blocks(low, stmt, paths)
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
        arrivals += arm(low, body, case.body, _binds(paths, binds), case.pattern.loc,
                        branch=stmt, drops=drops[index])
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
