"""The dataflow of the SLIR verifier: the moves and the drops of one function (docs/design/ir.md 8.13).

A dataflow over the CFG gives each local, at each point, the set of states that the paths to
that point can give it: uninit, live, moved or dropped, with the value of its drop flag for
a local that a `DropIfSet` reads. A join takes the union. A read or a move of a local that a
path moved is refused, and so is a read or a move of a local that holds no value on a path:
before its first write, or after its `StorageLive` or its `StorageDead`. The `return` needs
a value in `_0` on each path. An owning local must be moved or dropped exactly one time on
each path from its write to the end of its storage, to a write over it, and to the
`return`: a `DropIfSet` drops on the paths where its flag is true, and the paths where the
flag is false must have moved the local. An `Owned` parameter is an owning local; a
borrowed parameter never drops and never moves, and an `Inout` one holds a value again at
the `return`. A `Trap` ends the program, so it needs no drop.

The structure check of `verify.py` runs first and gives the dataflow its facts (`Check`).
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

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.verify import Check

# The state of a local on one path, and the value of its drop flag there.
_UNINIT, _LIVE, _MOVED, _DROPPED = "uninit", "live", "moved", "dropped"
_NO_FLAG, _UNSET, _TRUE, _FALSE, _UNKNOWN = "none", "unset", "true", "false", "unknown"
_Config: TypeAlias = tuple[str, str]
_State: TypeAlias = tuple[frozenset[_Config], ...]


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

    def run(self) -> None:
        check = self.check
        for local, flag in check.guards.items():
            self.flagged[flag] = (*self.flagged.get(flag, ()), local)
        start = tuple(frozenset({(_LIVE if local.id in check.conventions else _UNINIT,
                                  _UNSET if local.id in check.guards else _NO_FLAG)})
                      for local in check.function.locals)
        entry = check.function.entry
        states: dict[BlockId, _State] = {entry: start}
        work = [entry]
        while work:
            block = check.blocks[work.pop()]
            out = self.through(block, states[block.id])
            for target in successors(block.terminator):
                old = states.get(target)
                new = out if old is None else tuple(a | b for a, b in zip(old, out, strict=True))
                if new != old:
                    states[target] = new
                    work.append(target)
        self.report = True
        for block in check.function.blocks:
            if block.id in states:
                self.through(block, states[block.id])

    def through(self, block: SlirBlock, state: _State) -> _State:
        configs = list(state)
        for stmt in block.statements:
            self.statement(configs, stmt)
        self.terminator(configs, block.terminator)
        return tuple(configs)

    def violate(self, rule: str, detail: str, span: Optional[Span]) -> None:
        if self.report:
            self.check.fail(rule, detail, span)

    # -- the transfer --------------------------------------------------------------------

    def statement(self, configs: list[frozenset[_Config]], stmt: st.SlirStatement) -> None:
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

    def terminator(self, configs: list[frozenset[_Config]], term: tm.SlirTerminator) -> None:
        if isinstance(term, tm.SwitchInt):
            self.operand(configs, term.operand, term.span)
        elif isinstance(term, tm.Trap):
            for arg in term.args:
                self.operand(configs, arg, term.span)
        elif isinstance(term, tm.Return):
            self.leave(configs, term.span)

    def operand(self, configs: list[frozenset[_Config]], part: p.Operand | p.Place,
                span: Span) -> None:
        if isinstance(part, p.Const):
            return
        if isinstance(part, p.Move):
            self.move(configs, part.place, span)
        elif isinstance(part, p.Copy):
            self.read(configs, part.place, span)
        else:
            self.read(configs, part, span)

    def root(self, configs: list[frozenset[_Config]], place: p.Place,
             span: Span) -> Optional[LocalId]:
        """The root local of `place`, after the read of each index in it."""
        typed = self.check.place(place, span)
        for index in typed.indexes:
            self.operand(configs, index, span)
        return typed.root.id if isinstance(typed.root, p.Local) else None

    def read(self, configs: list[frozenset[_Config]], place: p.Place, span: Span) -> None:
        local = self.root(configs, place, span)
        if local is not None:
            self.spent(configs, local, f"reads {show(place)}", span)

    def spent(self, configs: list[frozenset[_Config]], local: LocalId, what: str,
              span: Span) -> None:
        states = {state for state, _ in configs[local.index]}
        if _UNINIT in states:
            self.violate(IN_STORAGE, f"{what} where _{local.index} holds no value", span)
        if _MOVED in states:
            self.violate(USE_AFTER_MOVE, f"{what} after a move of _{local.index}", span)
        if _DROPPED in states:
            self.violate(USE_AFTER_MOVE, f"{what} after a drop of _{local.index}", span)

    def move(self, configs: list[frozenset[_Config]], place: p.Place, span: Span) -> None:
        local = self.root(configs, place, span)
        if local is None:
            return
        if self.borrowed(local) or self.check.conventions.get(local) is Convention.INOUT:
            if local in self.check.owning:
                self.violate(DROP_ONCE, f"moves the parameter _{local.index}, which the "
                                        f"function does not own", span)
        self.spent(configs, local, f"moves {show(place)}", span)
        configs[local.index] = frozenset((_MOVED, flag) for _, flag in configs[local.index])

    def write(self, configs: list[frozenset[_Config]], place: p.Place, span: Span,
              value: Optional[r.Rvalue]) -> None:
        if not isinstance(place, p.Local):
            self.read(configs, place, span)
            return
        local = place.id
        if local in self.check.owning and any(s == _LIVE for s, _ in configs[local.index]):
            self.violate(DROP_ONCE, f"writes _{local.index} while it holds a value", span)
        configs[local.index] = frozenset((_LIVE, flag) for _, flag in configs[local.index])
        written = _UNKNOWN
        if (isinstance(value, r.Use) and isinstance(value.operand, p.Const)
                and isinstance(value.operand.constant, k.Bool)):
            written = _TRUE if value.operand.constant.value else _FALSE
        self.set_flag(configs, local, written)

    def set_flag(self, configs: list[frozenset[_Config]], flag: LocalId, value: str) -> None:
        for guarded in self.flagged.get(flag, ()):
            configs[guarded.index] = frozenset((s, value) for s, _ in configs[guarded.index])

    def drop(self, configs: list[frozenset[_Config]], local: LocalId, span: Span) -> None:
        if self.borrowed(local):
            self.violate(DROP_ONCE, f"drops the parameter _{local.index}, which the function "
                                    f"borrows", span)
        states = {s for s, _ in configs[local.index]}
        if _MOVED in states:
            self.violate(USE_AFTER_MOVE, f"drops _{local.index} after a move", span)
        if _DROPPED in states:
            self.violate(DROP_ONCE, f"drops _{local.index} two times", span)
        if _UNINIT in states:
            self.violate(DROP_ONCE, f"drops _{local.index}, which holds no value", span)
        configs[local.index] = frozenset((_DROPPED, flag) for _, flag in configs[local.index])

    def drop_if_set(self, configs: list[frozenset[_Config]], local: LocalId, flag: LocalId,
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
        configs[local.index] = frozenset(after)

    def storage(self, configs: list[frozenset[_Config]], local: LocalId, span: Span,
                verb: str) -> None:
        """`StorageLive` and `StorageDead`: each leaves the local with no value."""
        if local in self.check.owning and any(s == _LIVE for s, _ in configs[local.index]):
            self.violate(DROP_ONCE, f"{verb} the storage of _{local.index} while it holds a "
                                    f"value", span)
        configs[local.index] = frozenset((_UNINIT, flag) for _, flag in configs[local.index])
        self.set_flag(configs, local, _UNSET)

    def leave(self, configs: list[frozenset[_Config]], span: Span) -> None:
        """The `return`: no owning local holds a value, except `_0` and an `Inout` parameter,
        which must hold one. `_0` holds a value on every path."""
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

    def borrowed(self, local: LocalId) -> bool:
        convention = self.check.conventions.get(local)
        return convention is not None and convention.read_only
