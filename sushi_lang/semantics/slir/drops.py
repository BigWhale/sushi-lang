"""Drop placement: the one home of the drops that SHIR-to-SLIR lowering writes (docs/design/ir.md 8.10).

`DropPlacement` keeps the scopes of one function and the state of each local in them.
Lowering tells it what happens along its walk: a parameter, a block, the statement whose
temporaries it makes, the declaration of a local (in its block) or of a temporary (in its
statement), a move (from a `move` row of `borrow`, or a temporary that lowering moves),
a rebind, and the join of the paths that meet at a block. It answers with the statements
that a scope exit, a jump or a rebind needs. ONE method, `_exit`, places the statements
of every exit: for each local of the scopes that the path leaves, the newest declaration
first, its drop, then the `StorageDead` of its flag, then its own `StorageDead`. A function
exit then drops each `Owned` parameter that owns. A borrowed parameter never drops
(rule 5). The old value of a rebind drops when it may be live (rule 6).

The state of a local is live, moved or unknown. A move marks it moved, and a declaration or
a rebind marks it live. A join keeps a state that every path gives; a local whose state
differs between the paths becomes unknown, and it must have a drop flag (rule 4). The
state decides the drop: a live local gets `Drop`, a moved local nothing, an unknown local
`DropIfSet` with its flag (rule 3). The flag of a local is set `true` after its declaration
and at each rebind, and `false` after each move; the write of `false` waits in `_pending`
until lowering flushes it at the end of the statement or before a jump (L13).
Gate: `tests/unit/test_slir_drop_placement_is_one.py` (no other module builds a `Drop`).

The path state holds the open borrow regions too (ir.md 8.6): each view local whose region
is open, with the `BorrowId` of each owner and the statements of its last uses. An exit
ends the region of each view local of the scopes that it leaves, before the drops: so a
`return`, a `break`, a `continue` and the error path of a `??` end each region that they
cross, and the end of the storage of a temporary view ends its region. The paths that meet
at a join have the same open regions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.slir.ids import BorrowId, LocalId
from sushi_lang.semantics.typesys import BorrowMode

_LIVE, _MOVED, _UNKNOWN = "live", "moved", "unknown"


@dataclass(slots=True)
class _Scope:
    """A block, or a statement and its temporaries; the locals in declaration order."""
    statement: bool
    locals: list[LocalId] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OpenRegion:
    """The open borrow region of a view local: one `BorrowId` for each owner, and the
    statements of its last uses (none for a temporary, whose storage ends its region)."""
    view: LocalId
    ids: tuple[BorrowId, ...]
    until: frozenset[ShirId]


@dataclass(frozen=True, slots=True)
class FlowState:
    """The state of the locals on one path: the moved ones and the unknown ones, and the
    open borrow regions."""
    moved: frozenset[LocalId]
    unknown: frozenset[LocalId]
    regions: tuple[OpenRegion, ...] = ()

    def of(self, local: LocalId) -> str:
        if local in self.moved:
            return _MOVED
        return _UNKNOWN if local in self.unknown else _LIVE

    def changed_by(self, states: Sequence[FlowState],
                   among: Sequence[LocalId]) -> tuple[LocalId, ...]:
        """The locals of `among` whose state here (a loop header) is exact and that a path
        of `states` (a back edge) brings with another state."""
        return tuple(local for local in among if self.of(local) != _UNKNOWN
                     and any(state.of(local) != self.of(local) for state in states))


@dataclass(slots=True)
class DropPlacement:
    """The scopes of one function, the newest last, and the state of each local."""
    _scopes: list[_Scope] = field(default_factory=list)
    _owning: set[LocalId] = field(default_factory=set)
    _moved: set[LocalId] = field(default_factory=set)
    _unknown: set[LocalId] = field(default_factory=set)
    _flags: dict[LocalId, LocalId] = field(default_factory=dict)
    _pending: dict[LocalId, Span] = field(default_factory=dict)
    _owned_params: list[LocalId] = field(default_factory=list)
    _borrowed: set[LocalId] = field(default_factory=set)
    _never_moved: set[LocalId] = field(default_factory=set)
    _open: list[OpenRegion] = field(default_factory=list)
    _borrows: int = 0

    def parameter(self, local: LocalId, convention: Convention, owning: bool) -> None:
        """A parameter: an `Owned` one is an owning local of the function (L15)."""
        if owning:
            self._owning.add(local)
        if not convention.consumes:
            self._never_moved.add(local)
        if convention.read_only:
            self._borrowed.add(local)
        elif convention.consumes and owning:
            self._owned_params.append(local)

    def flag(self, local: LocalId, flag: LocalId) -> None:
        """`flag` is the drop flag of `local` (rule 4)."""
        self._flags[local] = flag

    def open_scope(self) -> None:
        """A block."""
        self._scopes.append(_Scope(statement=False))

    def open_statement(self) -> None:
        """A statement: its temporaries die at its end (L12)."""
        self._scopes.append(_Scope(statement=True))

    def declare(self, local: LocalId, owning: bool) -> None:
        """A local of the newest block. It holds its value: it is live."""
        blocks = [scope for scope in self._scopes if not scope.statement]
        if not blocks:
            er.raise_internal_error("CE0000", detail="a SLIR local outside every block")
        self._add(blocks[-1], local, owning)

    def temporary(self, local: LocalId, owning: bool) -> None:
        """A temporary of the statement that lowering is in. It is live."""
        if not self._scopes or not self._scopes[-1].statement:
            er.raise_internal_error("CE0000", detail="a SLIR temporary outside every statement")
        self._add(self._scopes[-1], local, owning)

    def detach(self, local: LocalId) -> None:
        """`local` leaves the newest scope: from here on lowering ends its storage itself
        (a condition local, at the top of each block that its switch goes to)."""
        self._scopes[-1].locals.remove(local)

    def ends_at_close(self, local: LocalId) -> bool:
        """Whether the end of the newest scope ends the storage of `local`."""
        return bool(self._scopes) and local in self._scopes[-1].locals

    def _add(self, scope: _Scope, local: LocalId, owning: bool) -> None:
        scope.locals.append(local)
        if owning:
            self._owning.add(local)

    def is_borrowed(self, local: LocalId) -> bool:
        """Whether `local` is a parameter that borrows the caller's value (rule 5)."""
        return local in self._borrowed

    def move(self, local: LocalId, span: Span) -> None:
        """A move out of `local`. A second move, a move of a local that may be moved, or a
        move of a parameter that the function does not own is an internal error: `borrow`
        refuses each of them."""
        if local in self._never_moved or local in self._moved or local in self._unknown:
            er.raise_internal_error(
                "CE0000", span=span,
                detail=f"SLIR lowering moves _{local.index}, which holds no value to move")
        self._moved.add(local)
        if local in self._flags:
            self._pending[local] = span

    def holds_live_value(self, local: LocalId) -> bool:
        """Whether a write to `local` may drop its old value first (rule 6)."""
        return local in self._owning and local not in self._moved

    def drop_old_value(self, local: LocalId, span: Span) -> list[st.SlirStatement]:
        """The drop of the old value of a rebind, if it may be live; the new value is live."""
        if local in self._borrowed and local in self._owning:
            er.raise_internal_error(
                "CE0000", span=span,
                detail=f"SLIR lowering rebinds _{local.index}, a borrow that it does not own")
        dropped = self._drop(local, span)
        self._moved.discard(local)
        self._unknown.discard(local)
        self._pending.pop(local, None)
        return dropped

    def _drop(self, local: LocalId, span: Span) -> list[st.SlirStatement]:
        """The drop of `local` by its state: `Drop`, `DropIfSet` or nothing (rule 3)."""
        if local not in self._owning or local in self._moved:
            return []
        if local in self._unknown:
            return [st.DropIfSet(p.Local(local), self._flags[local], span)]
        return [st.Drop(p.Local(local), span)]

    # -- drop flags (rule 4, L13) --------------------------------------------------------

    def flag_declared(self, local: LocalId, span: Span) -> list[st.SlirStatement]:
        """After the statement that declares `local`: its flag is live and `true`."""
        flag = self._flags.get(local)
        if flag is None:
            return []
        return [st.StorageLive(flag, span), _set(flag, True, span)]

    def flag_stored(self, local: LocalId, span: Span) -> list[st.SlirStatement]:
        """After the store of a rebind: the flag of `local` is `true` again."""
        flag = self._flags.get(local)
        return [] if flag is None else [_set(flag, True, span)]

    def flush(self) -> list[st.SlirStatement]:
        """The `false` writes of the flags of the locals moved since the last flush."""
        writes: list[st.SlirStatement] = [
            _set(self._flags[local], False, span)
            for local, span in self._pending.items() if local in self._moved]
        self._pending.clear()
        return writes

    # -- paths and joins -----------------------------------------------------------------

    def state(self) -> FlowState:
        """The state of the locals on the path that lowering is on."""
        return FlowState(frozenset(self._moved), frozenset(self._unknown), tuple(self._open))

    def restore(self, state: FlowState) -> None:
        """Go on along another path, from a state that `state` took."""
        self._moved = set(state.moved)
        self._unknown = set(state.unknown)
        self._open = list(state.regions)
        self._pending.clear()

    def in_scope(self) -> tuple[LocalId, ...]:
        """The owning locals that a join compares: each owned parameter and each owning
        local of the open scopes, in declaration order."""
        locals_ = [local for scope in self._scopes for local in scope.locals]
        return tuple(local for local in [*self._owned_params, *locals_] if local in self._owning)

    def join(self, states: Sequence[FlowState], among: Sequence[LocalId]) -> tuple[LocalId, ...]:
        """The state after the paths `states` meet: for each local of `among`, the state
        that every path gives, else unknown. The answer is each local whose states differ
        and that has no flag; it keeps the state of the first path, and lowering must not
        use the output of a walk that met one."""
        self.restore(states[0])
        if any(state.regions != states[0].regions for state in states):
            er.raise_internal_error("CE0000", detail="two paths meet with other open borrow "
                                                     "regions")
        lacking = []
        for local in among:
            if len({state.of(local) for state in states}) == 1:
                continue
            if local not in self._flags:
                lacking.append(local)
                continue
            self._moved.discard(local)
            self._unknown.add(local)
        return tuple(lacking)

    def mark_unknown(self, local: LocalId) -> None:
        """The state of `local` is not known here (a loop header that a back edge reaches
        with another state)."""
        if local not in self._flags:
            er.raise_internal_error("CE0000",
                                    detail=f"the unknown local _{local.index} has no drop flag")
        self._moved.discard(local)
        self._unknown.add(local)

    # -- borrow regions (ir.md 8.6) ------------------------------------------------------

    def begin_region(self, view: LocalId, owners: Sequence[p.Place], until: frozenset[ShirId],
                     span: Span) -> list[st.SlirStatement]:
        """The region of `view`: one `BeginBorrow` for each owner."""
        ids = tuple(BorrowId(self._borrows + at) for at in range(len(owners)))
        self._borrows += len(owners)
        self._open.append(OpenRegion(view, ids, until))
        return [st.BeginBorrow(ident, owner, BorrowMode.PEEK, span)
                for ident, owner in zip(ids, owners, strict=True)]

    def open_regions(self) -> tuple[OpenRegion, ...]:
        return tuple(self._open)

    def end_regions(self, ends: Callable[[OpenRegion], bool],
                    span: Span) -> list[st.SlirStatement]:
        """An `EndBorrow` for each owner of each open region that `ends` names, the newest
        region first."""
        ended = [region for region in self._open if ends(region)]
        self._open = [region for region in self._open if not ends(region)]
        return [st.EndBorrow(ident, span) for region in reversed(ended) for ident in region.ids]

    # -- exits ---------------------------------------------------------------------------

    def depth(self) -> int:
        """The count of the open scopes: a jump exits each scope after it."""
        return len(self._scopes)

    def exit_to(self, depth: int, span: Span) -> list[st.SlirStatement]:
        """A `break` or a `continue`: the exit of each scope after `depth`, the newest
        first. The scopes stay, because the statements that jump close them."""
        return self._exit(self._scopes[depth:], span)

    def close_scope(self, span: Span) -> list[st.SlirStatement]:
        """The end of the newest scope: the statements of its exit, and the scope goes."""
        statements = self._exit(self._scopes[-1:], span)
        self._scopes.pop()
        return statements

    def leave_scope(self) -> None:
        """The newest scope goes with no statement: the path left it by its own exit."""
        self._scopes.pop()

    def exit_function(self, span: Span) -> list[st.SlirStatement]:
        """A `return`: the exit of every scope, the newest first, then of each `Owned`
        parameter. The scopes stay, because the statement that returns closes them."""
        statements = self._exit(self._scopes, span)
        for local in reversed(self._owned_params):
            statements.extend(self._drop(local, span))
            if local in self._flags:
                statements.append(st.StorageDead(self._flags[local], span))
        return statements

    def _exit(self, scopes: list[_Scope], span: Span) -> list[st.SlirStatement]:
        """The exit of the scopes `scopes`: the end of the region of each view local of
        them, then each local, the newest declaration first, gets its drop by its state, then
        the `StorageDead` of its flag, then its own (rules 1 to 4)."""
        leaving = {local for scope in scopes for local in scope.locals}
        statements = self.end_regions(lambda region: region.view in leaving, span)
        for scope in reversed(scopes):
            for local in reversed(scope.locals):
                statements.extend(self._drop(local, span))
                flag = self._flags.get(local)
                if flag is not None:
                    statements.append(st.StorageDead(flag, span))
                statements.append(st.StorageDead(local, span))
        return statements


def _set(flag: LocalId, value: bool, span: Span) -> st.Assign:
    return st.Assign(p.Local(flag), r.Use(p.Const(k.Bool(value))), span)

