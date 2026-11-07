"""SHIR to SLIR: a function value, a call of a function value, and a lambda (docs/design/ir.md
8.6, 8.7, 8.9).

A named function as a value is `Aggregate(AggClosure(ty), [FnRef(symbol)])`, with no
environment. The symbol comes from the callee row of the `GlobalRef`, as for a call of the
function. A call of a function value is `Indirect(Operand)`, and the operand is the closure
value: a copy of its place, or a temporary of the statement. SLIR does not see the layout.

A `Lambda` cuts one `SlirFunction` from the tables of the enclosing body, with the symbol of
`LiftResults`. Its environment is `_1`, `InGuaranteed`, its declared parameters come after it,
and a capture is the place `Field(_1, k)`. The value is one `AggClosure` with the `FnRef`, and
a `Copy` or a `Move` of each captured local, by the `Ownership` row of the capture. The type
of the environment has its name from its layout, so two lambdas of one layout share one pair
of glue. This module reads `lower.py` for its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.channel import channel_result
from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Free, Indirect
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.typeck import lambda_conventions
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import STAGE, SlirFunction
from sushi_lang.semantics.slir.lower_values import Learned, Value
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, StructType, Type
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


def environment_struct(types: Sequence[Type]) -> StructType:
    """The type of an environment: one field for each capture, in slot order."""
    return StructType(name=interned_name("__closure_env", tuple(types)),
                      fields=tuple((f"_{slot}", ty) for slot, ty in enumerate(types)))


def lambda_value(low: Lowering, node: e.Lambda) -> Value:
    """The closure value, and the function that lowering cuts for it."""
    symbol = low.answers.lift.symbol_of(node.shir_id)
    captures = low.answers.lift.environment_of(node.shir_id)
    if symbol is None or captures is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a lambda with no row of lift reached SLIR")
    env = environment_struct([_capture_type(low, node, local) for local in captures])
    low.lifted.extend(_cut(low, node, symbol, env, captures))
    parts: list[p.Operand] = [p.Const(k.FnRef(symbol))]
    for local in captures:
        place = low.local_place(local, node.loc)
        moves = low.answers.ownership.capture_ownership_of(node.shir_id, local) is Ownership.MOVE
        parts.append(p.Move(place) if moves else p.Copy(place))
    return r.Aggregate(r.AggClosure(low.type_of(node), env), tuple(parts))


def _capture_type(low: Lowering, node: e.Lambda, local: ShirLocalId) -> Type:
    ty = low.typeck.capture_type(node.shir_id, local)
    if ty is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a capture with no type reached SLIR")
    return ty


def _return_type(low: Lowering, node: e.Lambda) -> Type:
    """The type of the return slot: the `Result` of a channel, else the answer of the lambda."""
    ty = low.type_of(node)
    if not isinstance(ty, FunctionType):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a lambda with no function type reached SLIR")
    if ty.err_type is None:
        return ty.ok_type
    result = channel_result(low.tables.structs, low.tables.enums, ty.ok_type, ty.err_type)
    if result is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a lambda with a channel has no Result type")
    return result


def _cut(low: Lowering, node: e.Lambda, symbol: str, env: StructType,
         captures: tuple[ShirLocalId, ...]) -> list[SlirFunction]:
    """The function of the lambda, then each function of a lambda in its body. As for a
    written body, the walk runs again with what it found, until it finds nothing new."""
    ret = _return_type(low, node)
    fn_type = low.type_of(node)
    has_channel = isinstance(fn_type, FunctionType) and fn_type.err_type is not None
    conventions = lambda_conventions(low.typeck, node)
    learned = Learned()
    while True:
        lowering = low.sibling(learned, node.body)
        lowering.expression_return = node.body.statements[0].shir_id if node.expr_body else None
        lowering.channel_expression = has_channel
        lowering.new_local(ret, None, None)
        environment = lowering.new_local(env, None, node.loc)
        lowering.placement.parameter(environment, Convention.IN_GUARANTEED, False)
        for slot, local in enumerate(captures):
            lowering.captures[local] = p.Capture(p.Local(environment), slot)
        params = lowering.bound_parameters(
            [(param.local, convention, node.loc)
             for param, convention in zip(node.params, conventions, strict=True)])
        lowering.lower_block(ret)
        found = lowering.all_learned()
        if found != learned:
            learned = found
            continue
        blocks = tuple(block.closed() for block in lowering.blocks)
        function = SlirFunction(symbol, symbol, low.unit, ((environment, Convention.IN_GUARANTEED),
                                                           *params),
                                tuple(lowering.locals), blocks, blocks[0].id, environment)
        return [function, *lowering.lifted]


def expression_return(low: Lowering, stmt: s.Return) -> None:
    """The `return` of an expression body. A channel returns the value in `Result.Ok`, and a
    bare `~` body evaluates a value that it does not return."""
    ret = low.locals[0].ty
    ty = low.type_of(stmt.value)
    if ret == BuiltinType.BLANK and ty != BuiltinType.BLANK:
        low.temporary(low.value(stmt.value), ty, stmt.value.loc)
        low.finish(r.Use(p.Const(k.Blank())), stmt.loc)
        return
    if not low.channel_expression:
        low.finish(low.value(stmt.value), stmt.loc)
        return
    operand = low.operand(stmt.value)
    if ty == BuiltinType.BLANK:
        operand = p.Const(k.Blank())
    low.finish(r.Aggregate(r.AggVariant(ret, "Ok"), (operand,)), stmt.loc)
