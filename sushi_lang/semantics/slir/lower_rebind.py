"""SHIR to SLIR: the rebind of a local and of a `var`, and the place of a `var` (docs/design/ir.md 8.9, 8.10).

A rebind of a local writes the new value first, then drops the old one, then stores, then
writes the flag (rules 4 and 6). A by-value parameter that holds the value of the caller
drops nothing (rule 5), and an address local drops the old value at its address (rule 8).
`s := s.concat(x)` of a local that holds a value of its own is the concat peephole: one
call of `string.concat_in_place` and no drop (8.9). A `var` is a `Global` place, and a
`var` that owns stops (`StorageRef owning`). Each arm is a free function that takes the
`Lowering` of `lower.py` first. This module reads `lower.py` for its types only.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.method_effects import CONCAT_IN_PLACE, STRING_FAMILY
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.slir import lower_calls as calls
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.ids import LocalId
from sushi_lang.semantics.slir.lower_values import CallValue, ShortCircuit
from sushi_lang.semantics.typesys import BuiltinType

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def rebind(low: Lowering, stmt: s.Assign, target: e.LocalRef) -> None:
    """A rebind: the new value first, then the drop of the old one, then the store, then
    the flag (rules 4 and 6). An old value that a move took, or that the caller owns,
    drops nothing. A new value that may still read the old one (a live owning value, `and`,
    `or`) goes into a temporary first."""
    ident = low.local_of(target.local, target.loc)
    if concat_in_place(low, stmt, target, ident):
        return
    value = low.value(stmt.value)
    if isinstance(value, ShortCircuit) or (low.placement.holds_live_value(ident) and not (
            isinstance(value, r.Use) and isinstance(value.operand, p.Const))):
        ty = low.type_of(stmt.value)
        temp = p.Local(low.temporary(value, ty, stmt.value.loc))
        value = r.Use(p.Move(temp) if low.owns(ty) else p.Copy(temp))
    low.emit(low.placement.drop_old_value(ident, stmt.loc))
    low.write(p.Local(ident), value, stmt.loc)
    low.emit(low.placement.flag_stored(ident, stmt.loc))


def concat_in_place(low: Lowering, stmt: s.Assign, target: e.LocalRef,
                    ident: LocalId) -> bool:
    """The concat peephole (ir.md 8.9): `s := s.concat(x)` of a local that holds a value of
    its own, and over which no borrow region is open, is ONE call of
    `string.concat_in_place` into a `~` temporary, with the receiver `Inout` and the
    argument `Guaranteed`, and no `Drop` of the old value. The argument is evaluated first.
    Answers False for any other rebind, which takes the general path."""
    node = stmt.value
    if not isinstance(node, e.Call) or len(node.args) != 1:
        return False
    resolved = low.typeck.callee_of(node.shir_id)
    callee = None if resolved is None else resolved.callee
    if not (isinstance(callee, Builtin) and callee.method.family == STRING_FAMILY
            and callee.method.name == "concat" and isinstance(callee.recv, e.LocalRef)
            and callee.recv.local == target.local):
        return False
    held = p.Local(ident)
    if not low.placement.holds_own_value(ident) or any(
            held in region.owners for region in low.placement.open_regions()):
        return False
    method = replace(callee.method, name=CONCAT_IN_PLACE)
    argument = calls.argument(low, node, 0, node.args[0])
    low.temporary(CallValue(st.Builtin(method, ()),
                            ((p.Copy(held), Convention.INOUT), argument)),
                  BuiltinType.BLANK, stmt.loc)
    return True


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
