"""The dataflow of the SLIR verifier: the moves and the drops of one function (docs/design/ir.md 8.13).

A dataflow over the CFG gives each local, at each point, the set of states that the paths to
that point can give it: uninit, live, moved or dropped, with the value of its drop flag for
a local that a `DropIfSet` reads. A join takes the union. A read or a move of a local that a
path moved is refused, and so is a read or a move of a local that holds no value on a path:
before its first write, or after its `StorageLive` or its `StorageDead`. The `return` needs
a value in `_0` on each path. An owning local must be moved or dropped exactly one time on
each path from its write to the end of its storage, to a write over it, and to the
`return`: a `DropIfSet` drops on the paths where its flag is true, and the paths where the
flag is false must have moved the local. An `Owned` parameter is an owning local; an
`InGuaranteed` parameter never drops and never moves, and an `Inout` one holds a value
again at the `return`. A `Guaranteed` parameter that owns starts lent: it holds the value of
the caller, which it reads and never drops or moves; a write gives it a value of its own,
and from there it is an owning local (rule 5). A `Trap` ends the program, so it needs no drop. The `Drop` of an element reads
its root local, which must hold a value, and does not change its state. A `Move` of a part
moves its root; a later `Move` of a disjoint part of that root in the same block is the same
take (`parts`), and a write, the storage or a drop of the root, or the end of the block, ends
it. A take spends the whole root: at its end, each owning part of the root that the take does
not move has a `Drop` in the block, of the part or of a part that holds it (`_missing_part`).
A lent part of the answer of a step (the `peeks` of the iterator) owns nothing of its own,
so it needs no `Drop`. The rule is read after each other rule of the dataflow.

The structure check of `verify.py` runs first and gives the dataflow its facts (`Check`).

The state of a block holds each local whose state a rule reads past a write: an owning
local, a guarded local and its flag, a parameter and `_0`. Any other local is in the state
only where it is live: where a path reaches a read of it before its next write or storage
(`_live_plain`). Elsewhere no rule reads its state, so the state of a block grows with the
live locals, not with all the locals of the function.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.slir.verify_rules import (
    DROP_ONCE, IN_STORAGE, RETURN_SLOT, USE_AFTER_MOVE,
)
from sushi_lang.semantics.typesys import (
    EnumType, IteratorType, StructType, deref_type, owns_resource,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.verify import Check

# The state of a local on one path, and the value of its drop flag there.
_UNINIT, _LIVE, _MOVED, _DROPPED, _LENT = "uninit", "live", "moved", "dropped", "lent"
_NO_FLAG, _UNSET, _TRUE, _FALSE, _UNKNOWN = "none", "unset", "true", "false", "unknown"
_Config: TypeAlias = tuple[str, str]
_Configs: TypeAlias = frozenset[_Config]
_State: TypeAlias = dict[int, _Configs]
_NO_VALUE: _Configs = frozenset({(_UNINIT, _NO_FLAG)})


def successors(term: tm.SlirTerminator) -> list[BlockId]:
    if isinstance(term, tm.Goto):
        return [term.target]
    if isinstance(term, tm.SwitchInt):
        targets = [target for _, target in term.targets]
        return targets if term.otherwise is None else [*targets, term.otherwise]
    return []


@dataclass(slots=True)
class Flow:
    """The dataflow of the moves and the drops of one function (see the module docstring)."""
    check: Check
    flagged: dict[LocalId, tuple[LocalId, ...]] = field(default_factory=dict)
    report: bool = False
    parts: dict[LocalId, list[p.Place]] = field(default_factory=dict)
    taken_at: dict[LocalId, Span] = field(default_factory=dict)
    dropped_parts: dict[LocalId, list[p.Place]] = field(default_factory=dict)
    left_parts: list[tuple[str, Span]] = field(default_factory=list)
    lent: dict[LocalId, list[p.Place]] = field(default_factory=dict)
    tracked: frozenset[int] = frozenset()

    def run(self) -> None:
        check = self.check
        for local, flag in check.guards.items():
            self.flagged[flag] = (*self.flagged.get(flag, ()), local)
        self.lent = _lent_parts(check)
        self.tracked = frozenset(
            local.index for local in (*check.owning, *check.guards, *check.guards.values(),
                                      *check.conventions, LocalId(0)))
        live = _live_plain(check, self.tracked)
        entry = check.function.entry
        start = {local.id.index: frozenset({(self.entry_state(local.id),
                                             _UNSET if local.id in check.guards else _NO_FLAG)})
                 for local in check.function.locals
                 if local.id.index in self.tracked or local.id.index in live[entry]}
        states: dict[BlockId, _State] = {entry: start}
        work = [entry]
        while work:
            block = check.blocks[work.pop()]
            out = self.through(block, states[block.id])
            for target in successors(block.terminator):
                kept = live[target]
                out_here = {index: configs for index, configs in out.items()
                            if index in self.tracked or index in kept}
                old = states.get(target)
                new = (out_here if old is None or old == out_here
                       else {index: old.get(index, _NO_VALUE) | out_here.get(index, _NO_VALUE)
                             for index in old.keys() | out_here.keys()})
                if new != old:
                    states[target] = new
                    work.append(target)
        self.report = True
        for block in check.function.blocks:
            if block.id in states:
                self.through(block, states[block.id])
        for detail, span in self.left_parts[:1]:
            self.violate(DROP_ONCE, detail, span)

    def through(self, block: SlirBlock, state: _State) -> _State:
        configs = dict(state)
        self.parts.clear()
        self.dropped_parts.clear()
        for stmt in block.statements:
            self.statement(configs, stmt)
        self.terminator(configs, block.terminator)
        for local in list(self.parts):
            self.end_take(local)
        return configs

    def entry_state(self, local: LocalId) -> str:
        """A parameter holds a value at the entry: a `Guaranteed` one that owns holds the
        value of the caller (lent). Any other local holds none."""
        convention = self.check.conventions.get(local)
        if convention is None:
            return _UNINIT
        if convention is Convention.GUARANTEED and local in self.check.owning:
            return _LENT
        return _LIVE

    def violate(self, rule: str, detail: str, span: Optional[Span]) -> None:
        if self.report:
            self.check.fail(rule, detail, span)

    # -- the transfer --------------------------------------------------------------------

    def statement(self, configs: _State, stmt: st.SlirStatement) -> None:
        span = stmt.span
        if isinstance(stmt, st.Assign):
            for part in r.operands_of(stmt.rvalue):
                self.operand(configs, part, span)
            self.write(configs, stmt.place, span, stmt.rvalue)
        elif isinstance(stmt, st.Call):
            if isinstance(stmt.callee, st.Indirect):
                self.operand(configs, stmt.callee.operand, span)
            for operand, _ in stmt.args:
                self.operand(configs, operand, span)
            self.write(configs, stmt.dest, span, None)
        elif isinstance(stmt, st.Drop) and isinstance(stmt.place,
                                                 (p.Field, p.Index, p.Downcast, p.Global)):
            self.read(configs, stmt.place, span)
            root = p.root_of(stmt.place)
            if isinstance(root, p.Local):
                self.dropped_parts.setdefault(root.id, []).append(stmt.place)
        elif isinstance(stmt, st.Drop):
            self.drop(configs, self.check.dropped(stmt), span)
        elif isinstance(stmt, st.DropIfSet):
            self.drop_if_set(configs, self.check.dropped(stmt), stmt.flag, span)
        elif isinstance(stmt, (st.StorageLive, st.StorageDead)):
            self.storage(configs, stmt.local, span,
                         "starts" if isinstance(stmt, st.StorageLive) else "ends")
        elif isinstance(stmt, st.BeginBorrow):
            self.read(configs, stmt.place, span)
        elif isinstance(stmt, st.EndBorrow):
            pass
        else:
            er.raise_internal_error("CE0000", span=span,
                                    detail=f"the dataflow of the SLIR verifier has no row for "
                                           f"the class {type(stmt).__name__}")

    def terminator(self, configs: _State, term: tm.SlirTerminator) -> None:
        if isinstance(term, tm.SwitchInt):
            self.operand(configs, term.operand, term.span)
        elif isinstance(term, tm.Trap):
            for arg in term.args:
                self.operand(configs, arg, term.span)
        elif isinstance(term, tm.Return):
            self.leave(configs, term.span)

    def operand(self, configs: _State, part: p.Operand | p.Place,
                span: Span) -> None:
        if isinstance(part, p.Const):
            return
        if isinstance(part, p.Move):
            self.move(configs, part.place, span)
        elif isinstance(part, p.Copy):
            self.read(configs, part.place, span)
        else:
            self.read(configs, part, span)

    def root(self, configs: _State, place: p.Place,
             span: Span) -> Optional[LocalId]:
        """The root local of `place`, after the read of each index in it."""
        typed = self.check.place(place, span)
        for index in typed.indexes:
            self.operand(configs, index, span)
        return typed.root.id if isinstance(typed.root, p.Local) else None

    def read(self, configs: _State, place: p.Place, span: Span) -> None:
        local = self.root(configs, place, span)
        if local is not None:
            self.spent(configs, local, f"reads {show(place)}", span)

    def spent(self, configs: _State, local: LocalId, what: str,
              span: Span) -> None:
        states = {state for state, _ in configs[local.index]}
        if _UNINIT in states:
            self.violate(IN_STORAGE, f"{what} where _{local.index} holds no value", span)
        if _MOVED in states:
            self.violate(USE_AFTER_MOVE, f"{what} after a move of _{local.index}", span)
        if _DROPPED in states:
            self.violate(USE_AFTER_MOVE, f"{what} after a drop of _{local.index}", span)

    def move(self, configs: _State, place: p.Place, span: Span) -> None:
        local = self.root(configs, place, span)
        if local is None:
            return
        convention = self.check.conventions.get(local)
        if convention is not None and not convention.consumes:
            if local in self.check.owning:
                self.violate(DROP_ONCE, f"moves the parameter _{local.index}, which the "
                                        f"function does not own", span)
        if self.another_part(configs, local, place):
            self.parts[local].append(place)
            return
        self.spent(configs, local, f"moves {show(place)}", span)
        _relabel(configs, local, _MOVED)
        if place != p.Local(local):
            self.parts[local] = [place]
            self.taken_at[local] = span

    def another_part(self, configs: _State, local: LocalId,
                     place: p.Place) -> bool:
        """Whether `place` is a part of `local` that one take moves after another part of
        it, earlier in this block: the parts are disjoint (ir.md 8.13)."""
        taken = self.parts.get(local)
        return (taken is not None and all(s == _MOVED for s, _ in configs[local.index])
                and all(not _within(one, place) and not _within(place, one)
                        for one in taken))

    def write(self, configs: _State, place: p.Place, span: Span,
              value: Optional[r.Rvalue]) -> None:
        if not isinstance(place, p.Local):
            self.read(configs, place, span)
            root = p.root_of(place)
            if isinstance(root, p.Local) and root.id in self.dropped_parts:
                self.dropped_parts[root.id] = [part for part in self.dropped_parts[root.id]
                                               if not _within(place, part)]
            return
        local = place.id
        self.end_take(local)
        if local in self.check.owning and any(s == _LIVE for s, _ in configs[local.index]):
            self.violate(DROP_ONCE, f"writes _{local.index} while it holds a value", span)
        _relabel(configs, local, _LIVE)
        written = _UNKNOWN
        if (isinstance(value, r.Use) and isinstance(value.operand, p.Const)
                and isinstance(value.operand.constant, k.Bool)):
            written = _TRUE if value.operand.constant.value else _FALSE
        self.set_flag(configs, local, written)

    def set_flag(self, configs: _State, flag: LocalId, value: str) -> None:
        for guarded in self.flagged.get(flag, ()):
            configs[guarded.index] = frozenset((s, value) for s, _ in configs[guarded.index])

    def drop(self, configs: _State, local: LocalId, span: Span) -> None:
        states = {s for s, _ in configs[local.index]}
        if self.borrowed(local) or _LENT in states:
            self.violate(DROP_ONCE, f"drops the parameter _{local.index}, which the function "
                                    f"borrows", span)
        if _MOVED in states:
            self.violate(USE_AFTER_MOVE, f"drops _{local.index} after a move", span)
        if _DROPPED in states:
            self.violate(DROP_ONCE, f"drops _{local.index} two times", span)
        if _UNINIT in states:
            self.violate(DROP_ONCE, f"drops _{local.index}, which holds no value", span)
        self.end_take(local)
        _relabel(configs, local, _DROPPED)

    def drop_if_set(self, configs: _State, local: LocalId, flag: LocalId,
                    span: Span) -> None:
        after = set()
        for state, value in configs[local.index]:
            if value not in (_TRUE, _FALSE):
                self.violate(DROP_ONCE, f"reads the flag _{flag.index} of _{local.index}, "
                                        f"which holds no known value", span)
            elif value == _TRUE and state != _LIVE:
                self.violate(DROP_ONCE, f"the flag _{flag.index} is true where _{local.index} "
                                        f"holds no value", span)
            elif value == _FALSE and state == _LIVE:
                self.violate(DROP_ONCE, f"the flag _{flag.index} is false where _{local.index} "
                                        f"holds a value", span)
            after.add((_DROPPED if state == _LIVE else state, value))
        self.end_take(local)
        configs[local.index] = frozenset(after)

    def storage(self, configs: _State, local: LocalId, span: Span,
                verb: str) -> None:
        """`StorageLive` and `StorageDead`: each leaves the local with no value."""
        if local in self.check.owning and any(s == _LIVE for s, _ in configs[local.index]):
            self.violate(DROP_ONCE, f"{verb} the storage of _{local.index} while it holds a "
                                    f"value", span)
        self.end_take(local)
        _relabel(configs, local, _UNINIT)
        self.set_flag(configs, local, _UNSET)

    def leave(self, configs: _State, span: Span) -> None:
        """The `return`: no owning local holds a value, except `_0` and an `Inout` parameter,
        which must hold one, and a `Guaranteed` parameter that holds the value of the caller.
        `_0` holds a value on every path."""
        for local in sorted(self.check.owning):
            if local.index == 0 or self.borrowed(local):
                continue
            states = {s for s, _ in configs[local.index]}
            if self.check.conventions.get(local) is Convention.INOUT:
                if states != {_LIVE}:
                    self.violate(DROP_ONCE, f"returns while the inout parameter "
                                            f"_{local.index} holds no value", span)
            elif _LIVE in states:
                self.violate(DROP_ONCE, f"returns while _{local.index} holds a value", span)
        if any(state != _LIVE for state, _ in configs[0]):
            self.violate(RETURN_SLOT, "returns before a write of _0", span)

    def end_take(self, local: LocalId) -> None:
        """The end of the take of `local`: each owning part that it does not move drops."""
        taken = self.parts.pop(local, None)
        dropped = self.dropped_parts.pop(local, [])
        span = self.taken_at.pop(local, None)
        if taken is None or local not in self.check.owning or span is None:
            return
        ends = [*taken, *dropped, *self.lent.get(local, ())]
        missing = _missing_part(self.check, p.Local(local), ends, span)
        if missing is not None and self.report:
            self.left_parts.append((f"the take of {show(taken[0])} leaves {show(missing)}, "
                                    f"which owns, with no drop", span))

    def borrowed(self, local: LocalId) -> bool:
        """Whether `local` is an `InGuaranteed` parameter: a view of the place of the
        caller."""
        return self.check.conventions.get(local) is Convention.IN_GUARANTEED


def _relabel(configs: _State, local: LocalId, state: str) -> None:
    """Give `local` the state `state` on each path, and keep the value of its flag."""
    configs[local.index] = frozenset(
        (state, flag) for _, flag in configs.get(local.index, _NO_VALUE))


def _live_plain(check: Check, tracked: frozenset[int]) -> dict[BlockId, frozenset[int]]:
    """For each block, the locals outside `tracked` that are live at its entry: a path from
    the entry reaches a read, a move or a drop of the local before a write of it or its
    storage. A local that a statement names counts as read there, so the set holds each
    local whose state a rule can read."""
    gen: dict[BlockId, frozenset[int]] = {}
    kill: dict[BlockId, frozenset[int]] = {}
    preds: dict[BlockId, list[BlockId]] = {block.id: [] for block in check.function.blocks}
    for block in check.function.blocks:
        used: set[int] = set()
        written: set[int] = set()
        for stmt in block.statements:
            used |= {index for index in _named(stmt)
                     if index not in tracked and index not in written}
            written |= {index for index in _written(stmt) if index not in tracked}
        used |= {index for index in _named_by(block.terminator)
                 if index not in tracked and index not in written}
        gen[block.id], kill[block.id] = frozenset(used), frozenset(written)
        for target in successors(block.terminator):
            preds[target].append(block.id)
    live: dict[BlockId, frozenset[int]] = {block.id: gen[block.id]
                                           for block in check.function.blocks}
    work = [block.id for block in check.function.blocks]
    while work:
        block = check.blocks[work.pop()]
        out = frozenset().union(*(live[target] for target in successors(block.terminator)))
        new = gen[block.id] | (out - kill[block.id])
        if new != live[block.id]:
            live[block.id] = new
            work.extend(preds[block.id])
    return live


def _read_locals(part: p.Operand | p.Place) -> tuple[int, ...]:
    if isinstance(part, p.Const):
        return ()
    place = part.place if isinstance(part, (p.Copy, p.Move)) else part
    return tuple(local.index for local in p.locals_read(place))


def _named(stmt: st.SlirStatement) -> list[int]:
    """The locals that `stmt` names, but not the local that it writes whole."""
    named = [index for part in st.operands_of(stmt) for index in _read_locals(part)]
    if isinstance(stmt, (st.Assign, st.Call)):
        dest = stmt.place if isinstance(stmt, st.Assign) else stmt.dest
        if not isinstance(dest, p.Local):
            named.extend(_read_locals(dest))
    elif isinstance(stmt, (st.Drop, st.BeginBorrow)):
        named.extend(_read_locals(stmt.place))
    elif isinstance(stmt, st.DropIfSet):
        named.extend((*_read_locals(stmt.place), stmt.flag.index))
    return named


def _written(stmt: st.SlirStatement) -> list[int]:
    """The local that `stmt` writes whole, or whose storage it starts or ends."""
    if isinstance(stmt, (st.StorageLive, st.StorageDead)):
        return [stmt.local.index]
    if isinstance(stmt, (st.Assign, st.Call)):
        dest = stmt.place if isinstance(stmt, st.Assign) else stmt.dest
        if isinstance(dest, p.Local):
            return [dest.id.index]
    return []


def _named_by(term: tm.SlirTerminator) -> list[int]:
    if isinstance(term, tm.SwitchInt):
        return list(_read_locals(term.operand))
    if isinstance(term, tm.Trap):
        return [index for arg in term.args for index in _read_locals(arg)]
    return []


def _within(outer: p.Place, place: p.Place) -> bool:
    """Whether `place` is `outer` or a projection of it."""
    while True:
        if place == outer:
            return True
        if not isinstance(place, (p.Field, p.Index, p.Downcast, p.Deref)):
            return False
        place = place.base


def _lent_parts(check: Check) -> dict[LocalId, list[p.Place]]:
    """The lent parts of each local that the step of an iterator writes: the item of the
    `Some`, or each lent element of a tuple item (the `peeks` of the iterator)."""
    lent: dict[LocalId, list[p.Place]] = {}
    for block in check.function.blocks:
        for stmt in block.statements:
            if not (isinstance(stmt, st.Call) and isinstance(stmt.callee, st.Indirect)
                    and isinstance(stmt.dest, p.Local)):
                continue
            ty = check.operand_type(stmt.callee.operand, stmt.span)
            walked = None if ty is None else deref_type(ty)
            peeks = walked.peeks if isinstance(walked, IteratorType) else ()
            item = p.Field(p.Downcast(stmt.dest, "Some"), 0)
            parts = [item] if peeks == (True,) else [
                p.Field(item, index) for index, peek in enumerate(peeks) if peek]
            if parts:
                lent.setdefault(stmt.dest.id, []).extend(parts)
    return lent


def _missing_part(check: Check, place: p.Place, ends: list[p.Place],
                  span: Span) -> Optional[p.Place]:
    """The first owning part of `place` that no place of `ends` holds, and that is not
    split into parts that the places of `ends` hold. None when there is no such part."""
    if any(_within(end, place) for end in ends):
        return None
    inside = [end for end in ends if _within(place, end)]
    if not inside:
        ty = check.operand_type(p.Copy(place), span)
        return place if ty is not None and owns_resource(ty, check.drops) else None
    for part in _parts_of(check, place, inside, span):
        missing = _missing_part(check, part, ends, span)
        if missing is not None:
            return missing
    return None


def _parts_of(check: Check, place: p.Place, inside: list[p.Place],
              span: Span) -> list[p.Place]:
    """The parts of `place` that a take splits: each field of a struct or of the payload of
    a variant, and each variant of an enum that a place of `inside` reads."""
    ty = check.place(place, span).ty
    if isinstance(ty, EnumType):
        variants = [p.Downcast(place, variant.name) for variant in ty.variants]
        return [part for part in variants if any(_within(part, end) for end in inside)]
    if isinstance(place, p.Downcast):
        enum = check.place(place.base, span).ty
        variant = enum.get_variant(place.variant) if isinstance(enum, EnumType) else None
        count = 0 if variant is None else len(variant.associated_types)
    elif isinstance(ty, StructType):
        count = len(ty.fields)
    else:
        return []
    return [p.Field(place, index) for index in range(count)]
