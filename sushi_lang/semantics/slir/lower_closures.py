"""SHIR to SLIR: a function value, and a call of a function value (docs/design/ir.md 8.6, 8.7).

A named function as a value is `Aggregate(AggClosure(ty), [FnRef(symbol)])`, with no
environment. The symbol comes from the callee row of the `GlobalRef`, as for a call of the
function. A call of a function value is `Indirect(Operand)`, and the operand is the closure
value: a copy of its place, or a temporary of the statement. SLIR does not see the layout.
This module reads `lower.py` for its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Free, Indirect
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.lower_values import Value
from sushi_lang.semantics.unit_symbols import function_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering

_PLACES = (e.LocalRef, e.StorageRef, e.Field, e.Index, e.Slice)


def function_value(low: Lowering, node: e.GlobalRef) -> Value:
    resolved = low.typeck.callee_of(node.shir_id)
    if resolved is None or not isinstance(resolved.callee, Free):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a function value with no callee reached SLIR")
    if resolved.unit is None:
        raise StageStop(STAGE, "library function", node.loc)
    name = low.decls.name_of(resolved.callee.decl)
    if resolved.callee.decl == node.decl:
        name = name.rpartition(".")[2]
    symbol = function_symbol(name, resolved.unit)
    return r.Aggregate(r.AggClosure(low.type_of(node)), (p.Const(k.FnRef(symbol)),))


def indirect(low: Lowering, node: e.Call, callee: Indirect) -> st.SlirCallee:
    """The closure value, read before each argument that can call."""
    if callee.field is None:
        later = [arg.value for arg in node.args]
        return st.Indirect(agg.settled(low, callee.value, low.operand(callee.value), later))
    index = low.typeck.field_index_of(node.shir_id)
    if index is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a call of a field with no index reached SLIR")
    value = callee.value
    base = (agg.place_of(low, value) if isinstance(value, _PLACES)
            else p.Local(low.temporary(low.value(value), low.type_of(value), value.loc)))
    return st.Indirect(p.Copy(p.Field(base, index)))
