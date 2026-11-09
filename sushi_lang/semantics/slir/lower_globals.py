"""SHIR to SLIR: the place of a `var`, and its rebind (docs/design/ir.md 8.4, 8.9, 8.10).

A `var` is a `Global` place, named by the symbol of its typecheck row. A `Global` that owns
is a place as a local is, with no scope: its rebind puts the new value into a temporary,
unless it is a constant, then drops the old value, then moves the temporary into the
global (rule 6). No exit drops it. A borrowed read of one is a `Copy` of the place
(`Lowering.operand`). Each arm is a free function that takes the `Lowering` of `lower.py`
first. This module reads `lower.py` for its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir.lower_values import ShortCircuit

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def global_place(low: Lowering, ref: e.StorageRef) -> p.Global:
    """The `Global` of the `var` that `ref` names, by the symbol of its typecheck row."""
    symbol = low.typeck.global_of(ref.shir_id)
    if symbol is None:
        er.raise_internal_error("CE0000", span=ref.loc,
                                detail="a unit variable with no global reached SLIR")
    return p.Global(symbol, low.type_of(ref))


def owner_place(low: Lowering, symbol: str, span: Span) -> p.Global:
    """The `Global` of an owner of a region row of `borrow`, which names a `var` by the
    symbol of its global."""
    ty = low.typeck.global_type(symbol)
    if ty is None:
        er.raise_internal_error("CE0000", span=span,
                                detail="a region over a unit variable that the body does "
                                       "not name reached SLIR")
    return p.Global(symbol, ty)


def rebind_global(low: Lowering, stmt: s.Assign, target: e.StorageRef) -> None:
    """`v := x` of a `var`: the new value, then the store into its global. A plain `var`
    drops nothing, and `and` and `or` go into a temporary first, because the right operand
    can read the `var` again. A `var` that owns drops its old value right before the
    store, which moves the temporary of the new value (ir.md 8.10, rule 6)."""
    place = global_place(low, target)
    value = low.value(stmt.value)
    ty = low.type_of(stmt.value)
    if not low.owns(place.ty):
        if isinstance(value, ShortCircuit):
            value = r.Use(p.Copy(p.Local(low.temporary(value, ty, stmt.value.loc))))
        low.write(place, value, stmt.loc)
        return
    if not (isinstance(value, r.Use) and isinstance(value.operand, p.Const)):
        value = r.Use(p.Move(p.Local(low.temporary(value, ty, stmt.value.loc))))
    low.emit(low.placement.drop_global(place, stmt.loc))
    low.write(place, value, stmt.loc)
