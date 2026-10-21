"""Places, operands, constants and rvalues of SLIR as LLVM values (docs/design/ir.md 8.4, 8.5, 8.7).

An operator calls the emitter of the old backend that takes LLVM values (ruling Q7), so the
integer and the float rules stay in one place, and the backend folds nothing
(`semantics/integer_ops.py` is the one compile-time home). An rvalue over a type that is
not an integer, a float or a `bool` stops with the class of the rvalue.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from llvmlite import ir

from sushi_lang.backend.expressions.casts import emit_value_cast
from sushi_lang.backend.expressions.literals import emit_int_constant
from sushi_lang.backend.expressions.operators import (
    emit_arithmetic, emit_bitwise, emit_number_comparison, emit_unary_value, emit_xor,
)
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.semantics.operators import ARITHMETIC_OPS, BITWISE_OPS, COMPARISON_OPS, SHIFT_OPS
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen



def place_address(state: FunctionState, place: p.Place) -> ir.Value:
    """The address of a place. A local is its storage; a projection stops for now."""
    if isinstance(place, p.Local):
        return state.storage_of(place.id)
    state.stop(place)


def type_of_place(state: FunctionState, place: p.Place) -> Type:
    if isinstance(place, p.Local):
        return state.type_of(place.id)
    state.stop(place)


def operand_value(codegen: LLVMCodegen, state: FunctionState, operand: p.Operand) -> ir.Value:
    """The value of an operand: a load of its place, or a constant. The drop statements of
    SLIR carry the ownership, so `Copy` and `Move` read alike."""
    match operand:
        case p.Const(constant):
            return constant_value(codegen, state, constant)
        case p.Copy(place) | p.Move(place):
            return state.builder.load(place_address(state, place))


def type_of_operand(state: FunctionState, operand: p.Operand) -> Type:
    match operand:
        case p.Const(constant):
            return type_of_constant(state, constant)
        case p.Copy(place) | p.Move(place):
            return type_of_place(state, place)


def constant_value(codegen: LLVMCodegen, state: FunctionState, constant: k.Constant) -> ir.Value:
    if isinstance(constant, k.Int):
        return emit_int_constant(codegen, constant.ty, constant.value)
    if isinstance(constant, (k.Float, k.Bool, k.Blank)):
        return ir.Constant(codegen.types.ll_type(type_of_constant(state, constant)),
                           _scalar(constant))
    state.stop(constant)


def type_of_constant(state: FunctionState, constant: k.Constant) -> Type:
    if isinstance(constant, (k.Int, k.Float)):
        return constant.ty
    if isinstance(constant, k.Bool):
        return BuiltinType.BOOL
    if isinstance(constant, k.Blank):
        return BuiltinType.BLANK
    state.stop(constant)


def _scalar(constant: k.Float | k.Bool | k.Blank) -> float | int:
    if isinstance(constant, k.Float):
        return float(constant.value)
    if isinstance(constant, k.Bool):
        return int(constant.value)
    return 0


def rvalue_value(codegen: LLVMCodegen, state: FunctionState, rvalue: r.Rvalue) -> ir.Value:
    """The value of an rvalue, from the one table of the rvalue classes."""
    emit = _RVALUES.get(type(rvalue))
    if emit is None:
        state.stop(rvalue)
    return emit(codegen, state, rvalue)


def _use(codegen: LLVMCodegen, state: FunctionState, rvalue: r.Use) -> ir.Value:
    return operand_value(codegen, state, rvalue.operand)


def _binary(codegen: LLVMCodegen, state: FunctionState, rvalue: r.Binary) -> ir.Value:
    ty = _scalar_type(state, rvalue, type_of_operand(state, rvalue.left))
    _scalar_type(state, rvalue, type_of_operand(state, rvalue.right))
    left = operand_value(codegen, state, rvalue.left)
    right = operand_value(codegen, state, rvalue.right)
    op = rvalue.op
    if op in ARITHMETIC_OPS:
        return emit_arithmetic(codegen, op, left, right, ty)
    if op in BITWISE_OPS + SHIFT_OPS:
        return emit_bitwise(codegen, op, left, right, ty)
    if op in COMPARISON_OPS:
        answer = emit_number_comparison(codegen, op, left, right, ty)
        return state.builder.zext(answer, codegen.types.ll_type(BuiltinType.BOOL))
    if op == "xor":
        return emit_xor(codegen, left, right, to_i1=False)
    state.fault(f"the operator '{op}' is control flow, never an rvalue")


def _unary(codegen: LLVMCodegen, state: FunctionState, rvalue: r.Unary) -> ir.Value:
    _scalar_type(state, rvalue, type_of_operand(state, rvalue.operand))
    return emit_unary_value(codegen, rvalue.op, operand_value(codegen, state, rvalue.operand),
                            to_i1=False)


def _cast(codegen: LLVMCodegen, state: FunctionState, rvalue: r.Cast) -> ir.Value:
    source = _scalar_type(state, rvalue, type_of_operand(state, rvalue.operand))
    _scalar_type(state, rvalue, rvalue.target)
    return emit_value_cast(codegen, operand_value(codegen, state, rvalue.operand), source,
                           rvalue.target)


def _scalar_type(state: FunctionState, rvalue: r.Rvalue, ty: Type) -> Type:
    """`ty` when it is an integer, a float or a `bool`; else the rvalue stops."""
    if ty != BuiltinType.BOOL and not is_numeric_type(ty):
        state.stop(rvalue)
    return ty


_RVALUES: dict[type, Callable[[LLVMCodegen, FunctionState, Any], ir.Value]] = {
    r.Use: _use,
    r.Binary: _binary,
    r.Unary: _unary,
    r.Cast: _cast,
}
