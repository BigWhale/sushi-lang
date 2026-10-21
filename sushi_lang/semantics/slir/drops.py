"""Drop placement: the one home of the drops that SHIR-to-SLIR lowering writes (docs/design/ir.md 8.10).

`DropPlacement` keeps the scopes of one function and the state of each local in them.
Lowering tells it what happens along its walk: a parameter, a block, the statement whose
temporaries it makes, the declaration of a local (in its block) or of a temporary (in its
statement), a move (from a `move` row of `borrow`, or a temporary that lowering moves),
and a rebind. It answers with the statements that a scope exit or a rebind needs. ONE
function, `_exit`, places the statements of every exit: for each local of the scopes that
the path leaves, the newest declaration first, a `Drop` when the local owns and is live,
then its `StorageDead`. A function exit then drops each `Owned` parameter that owns and is
live. A borrowed parameter never drops (rule 5). The old value of a rebind drops when it
is live (rule 6). A live local drops, and a moved local does not (rule 3).

The state of a local is live or moved. A move marks it moved, and a declaration or a
rebind marks it live. In the straight line the state is exact; the join of two paths, and
the drop flags of a local whose state differs between them (rule 4), are not built yet.
Gate: `tests/unit/test_slir_drop_placement_is_one.py` (no other module builds a `Drop`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.ids import LocalId

#: The conventions whose parameter is the caller's value: it never drops and never moves.
_NEVER_MOVES = frozenset({Convention.GUARANTEED, Convention.IN_GUARANTEED, Convention.INOUT})
#: The conventions whose parameter is a read-only borrow: a rebind of it is not lowered.
_BORROWS = frozenset({Convention.GUARANTEED, Convention.IN_GUARANTEED})


@dataclass(slots=True)
class _Scope:
    """A block, or a statement and its temporaries; the locals in declaration order."""
    statement: bool
    locals: list[LocalId] = field(default_factory=list)


@dataclass(slots=True)
class DropPlacement:
    """The scopes of one function, the newest last, and the state of each local."""
    _scopes: list[_Scope] = field(default_factory=list)
    _owning: set[LocalId] = field(default_factory=set)
    _moved: set[LocalId] = field(default_factory=set)
    _owned_params: list[LocalId] = field(default_factory=list)
    _borrowed: set[LocalId] = field(default_factory=set)
    _never_moved: set[LocalId] = field(default_factory=set)

    def parameter(self, local: LocalId, convention: Convention, owning: bool) -> None:
        """A parameter: an `Owned` one is an owning local of the function (L15)."""
        if owning:
            self._owning.add(local)
        if convention in _NEVER_MOVES:
            self._never_moved.add(local)
        if convention in _BORROWS:
            self._borrowed.add(local)
        elif convention is Convention.OWNED and owning:
            self._owned_params.append(local)

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

    def _add(self, scope: _Scope, local: LocalId, owning: bool) -> None:
        scope.locals.append(local)
        if owning:
            self._owning.add(local)

    def is_borrowed(self, local: LocalId) -> bool:
        """Whether `local` is a parameter that borrows the caller's value (rule 5)."""
        return local in self._borrowed

    def move(self, local: LocalId, span: Span) -> None:
        """A move out of `local`. A second move, or a move of a parameter that the function
        does not own, is an internal error: `borrow` refuses both."""
        if local in self._never_moved or local in self._moved:
            er.raise_internal_error(
                "CE0000", span=span,
                detail=f"SLIR lowering moves _{local.index}, which holds no value to move")
        self._moved.add(local)

    def holds_live_value(self, local: LocalId) -> bool:
        """Whether a write to `local` must drop its old value first (rule 6)."""
        return local in self._owning and local not in self._moved

    def drop_old_value(self, local: LocalId, span: Span) -> list[st.SlirStatement]:
        """The drop of the old value of a rebind, if it is live; the new value is live."""
        if local in self._borrowed and local in self._owning:
            er.raise_internal_error(
                "CE0000", span=span,
                detail=f"SLIR lowering rebinds _{local.index}, a borrow that it does not own")
        dropped: list[st.SlirStatement] = (
            [st.Drop(p.Local(local), span)] if self.holds_live_value(local) else [])
        self._moved.discard(local)
        return dropped

    def close_scope(self, span: Span) -> list[st.SlirStatement]:
        """The end of the newest scope: the statements of its exit, and the scope goes."""
        statements = _exit(self, self._scopes[-1:], span)
        self._scopes.pop()
        return statements

    def leave_scope(self) -> None:
        """The newest scope goes with no statement: the path left it by its own exit."""
        self._scopes.pop()

    def exit_function(self, span: Span) -> list[st.SlirStatement]:
        """A `return`: the exit of every scope, the newest first, then of each `Owned`
        parameter. The scopes stay, because the statement that returns closes them."""
        statements = _exit(self, self._scopes, span)
        statements.extend(st.Drop(p.Local(local), span) for local in reversed(self._owned_params)
                          if local not in self._moved)
        return statements


def _exit(placement: DropPlacement, scopes: list[_Scope],
          span: Span) -> list[st.SlirStatement]:
    """The exit of the scopes `scopes`: each local, the newest declaration first, gets a
    `Drop` when it owns and is live, then its `StorageDead` (rules 1, 2 and 3)."""
    statements: list[st.SlirStatement] = []
    for scope in reversed(scopes):
        for local in reversed(scope.locals):
            if placement.holds_live_value(local):
                statements.append(st.Drop(p.Local(local), span))
            statements.append(st.StorageDead(local, span))
    return statements
