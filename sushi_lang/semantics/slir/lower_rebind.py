"""SHIR to SLIR: the rebind of a local and of a `var`, and the place of a `var` (docs/design/ir.md 8.9, 8.10).

A rebind of a local writes the new value first, then drops the old one, then stores, then
writes the flag (rules 4 and 6). A `var` is a `Global` place, and a `var` that owns stops
(`StorageRef owning`). Each arm is a free function that takes the `Lowering` of `lower.py`
first. This module reads `lower.py` for its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.lower_values import ShortCircuit

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def rebind(low: Lowering, stmt: s.Assign, target: e.LocalRef) -> None:
    """A rebind: the new value first, then the drop of the old one, then the store, then
    the flag (rules 4 and 6). An old value that a move took drops nothing. A new value
    that may still read the old one (a live owning value, `and`, `or`) goes into a
    temporary first."""
    ident = low.local_of(target.local, target.loc)
    owning = low.owns(low.locals[ident.index].ty)
    if owning and (low.placement.is_borrowed(ident) or low.placement.is_address(ident)):
        raise StageStop(STAGE, "borrowed rebind", stmt.loc)
    value = low.value(stmt.value)
    if isinstance(value, ShortCircuit) or (low.placement.holds_live_value(ident) and not (
            isinstance(value, r.Use) and isinstance(value.operand, p.Const))):
        ty = low.type_of(stmt.value)
        temp = p.Local(low.temporary(value, ty, stmt.value.loc))
        value = r.Use(p.Move(temp) if low.owns(ty) else p.Copy(temp))
    low.emit(low.placement.drop_old_value(ident, stmt.loc))
    low.write(p.Local(ident), value, stmt.loc)
    low.emit(low.placement.flag_stored(ident, stmt.loc))


def rebind_global(low: Lowering, stmt: s.Assign, target: e.StorageRef) -> None:
    """`v := x` of a `var`: the new value, then the store into its global. A plain
    `var` drops nothing. `and` and `or` go into a temporary first, because the right
    operand can read the `var` again."""
    place = global_place(low, target)
    value = low.value(stmt.value)
    if isinstance(value, ShortCircuit):
        value = r.Use(p.Copy(p.Local(low.temporary(value, low.type_of(stmt.value),
                                                   stmt.value.loc))))
    low.write(place, value, stmt.loc)


def global_place(low: Lowering, ref: e.StorageRef) -> p.Global:
    """The `Global` of the `var` that `ref` names, by the symbol of its typecheck row. A
    `var` that owns stops (`StorageRef owning`): its rebind drops, and that is group
    D1."""
    symbol = low.typeck.global_of(ref.shir_id)
    if symbol is None:
        er.raise_internal_error("CE0000", span=ref.loc,
                                detail="a unit variable with no global reached SLIR")
    ty = low.type_of(ref)
    if low.owns(ty):
        dispatch.stop(STAGE, ref, state="owning")
    return p.Global(symbol, ty)
