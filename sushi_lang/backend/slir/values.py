"""Places, operands, constants and rvalues of SLIR as LLVM values (docs/design/ir.md 8.4, 8.5, 8.7).

An operator calls the emitter of the old backend that takes LLVM values (ruling Q7), so the
integer and the float rules stay in one place, and the backend folds nothing
(`semantics/integer_ops.py` is the one compile-time home). An rvalue over a type that is
not an integer, a float, a `bool` or an enum comparison stops with the class of the rvalue.

An enum is `{i32 tag, [K x i64] data}`. The tag, a variant and the address of a payload
field come from the one layout authority (`TypeSizing.payload_field_offsets`, read through
`enum_utils` and the enum emitter), at the semantic type of the place, never from a layout
match. An `Aggregate` of a variant, as an rvalue and as a constant, goes through one check
of the variant against the semantic types of its payload (`checked_variant`). The rvalue
calls it and then the enum emitter; the constant goes through the one converter of an enum
constant (`enum_initializer`), which runs the check itself and which the global of a
`const` reads too.
"""
from __future__ import annotations

from typing import Any, Callable

from llvmlite import ir

from sushi_lang.backend import enum_utils
from sushi_lang.backend.constants.enum_initializers import (
    checked_variant, enum_initializer, enum_of, variant_of,
)
from sushi_lang.backend.expressions.casts import emit_value_cast
from sushi_lang.backend.expressions.enums import emit_enum_variant_value
from sushi_lang.backend.expressions.literals import emit_int_constant
from sushi_lang.backend.expressions.operators import (
    emit_arithmetic, emit_bitwise, emit_contract_relation, emit_number_comparison, emit_string_relation,
    emit_unary_value, emit_xor,
)
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.semantics.operators import ARITHMETIC_OPS, BITWISE_OPS, COMPARISON_OPS, SHIFT_OPS
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type


def place_address(state: FunctionState, place: p.Place) -> ir.Value:
    """The address of a place: the storage of a local, or a payload field of a variant."""
    match place:
        case p.Local(local):
            return state.storage_of(local)
        case p.Field(p.Downcast(base, variant), index):
            types = _payload_types(state, base, variant, index)
            offsets = state.codegen.types.payload_field_offsets(types)
            data = enum_utils.get_data_ptr(state.codegen, place_address(state, base))
            return enum_utils.variant_field_address(state.codegen, data, types[index],
                                                    offsets[index])
    state.stop(place)


def type_of_place(state: FunctionState, place: p.Place) -> Type:
    match place:
        case p.Local(local):
            return state.type_of(local)
        case p.Field(p.Downcast(base, variant), index):
            return _payload_types(state, base, variant, index)[index]
    state.stop(place)


def _payload_types(state: FunctionState, base: p.Place, variant: str,
                   index: int) -> tuple[Type, ...]:
    """The payload types of the variant `variant` of the enum in `base`, which has a
    payload field `index`."""
    _, info = variant_of(type_of_place(state, base), variant, state.fault)
    if not 0 <= index < len(info.associated_types):
        state.fault(f"the variant '{variant}' of {base} has no field {index}")
    return tuple(info.associated_types)


def operand_value(state: FunctionState, operand: p.Operand) -> ir.Value:
    """The value of an operand: a load of its place, or a constant. The drop statements of
    SLIR carry the ownership, so `Copy` and `Move` read alike."""
    match operand:
        case p.Const(constant):
            return constant_value(state, constant)
        case p.Copy(place) | p.Move(place):
            return state.builder.load(place_address(state, place))


def type_of_operand(state: FunctionState, operand: p.Operand) -> Type:
    match operand:
        case p.Const(constant):
            return type_of_constant(state, constant)
        case p.Copy(place) | p.Move(place):
            return type_of_place(state, place)


def constant_value(state: FunctionState, constant: k.Constant) -> ir.Value:
    """An LLVM value of a constant. A string literal comes from the string constant seam,
    and it owns no heap (its owned flag is 0). A variant goes through the one converter of an
    enum constant. A struct stops (`emit, aggregate constant`) until the emit chunk of A5."""
    codegen = state.codegen
    if isinstance(constant, k.Int):
        return emit_int_constant(codegen, constant.ty, constant.value)
    if isinstance(constant, (k.Float, k.Bool, k.Blank)):
        return ir.Constant(codegen.types.ll_type(type_of_constant(state, constant)),
                           _scalar(constant))
    if isinstance(constant, k.Str):
        return codegen.runtime.strings.emit_string_literal(
            constant.value.decode("utf-8", "surrogatepass"))
    if isinstance(constant, k.Aggregate) and constant.variant is not None:
        return enum_initializer(constant, codegen.types, _literal_constant(state), "",
                                span=state.span, filename=state.filename)
    state.stop("aggregate constant" if isinstance(constant, k.Aggregate) else constant)


def _literal_constant(state: FunctionState) -> Callable[[str, str], ir.Constant]:
    """The string finisher of a constant payload: the constant fat pointer of a literal."""
    strings = state.codegen.runtime.strings
    return lambda text, _name: strings.string_literal_constant(text)


def type_of_constant(state: FunctionState, constant: k.Constant) -> Type:
    if isinstance(constant, (k.Int, k.Float)):
        return constant.ty
    if isinstance(constant, k.Bool):
        return BuiltinType.BOOL
    if isinstance(constant, k.Blank):
        return BuiltinType.BLANK
    if isinstance(constant, k.Str):
        return BuiltinType.STRING
    if isinstance(constant, k.Aggregate) and constant.variant is not None:
        return constant.ty
    state.stop("aggregate constant" if isinstance(constant, k.Aggregate) else constant)


def _scalar(constant: k.Float | k.Bool | k.Blank) -> float | int:
    if isinstance(constant, k.Float):
        return float(constant.value)
    if isinstance(constant, k.Bool):
        return int(constant.value)
    return 0


def rvalue_value(state: FunctionState, rvalue: r.Rvalue) -> ir.Value:
    """The value of an rvalue, from the one table of the rvalue classes."""
    emit = _RVALUES.get(type(rvalue))
    if emit is None:
        state.stop(rvalue)
    return emit(state, rvalue)


def _use(state: FunctionState, rvalue: r.Use) -> ir.Value:
    return operand_value(state, rvalue.operand)


def _binary(state: FunctionState, rvalue: r.Binary) -> ir.Value:
    codegen = state.codegen
    if _is_string_relation(state, rvalue):
        answer = emit_string_relation(codegen, rvalue.op, operand_value(state, rvalue.left),
                                      operand_value(state, rvalue.right))
        return state.builder.zext(answer, codegen.types.ll_type(BuiltinType.BOOL))
    if _is_contract_relation(state, rvalue):
        answer = emit_contract_relation(codegen, rvalue.op, operand_value(state, rvalue.left),
                                        operand_value(state, rvalue.right),
                                        type_of_operand(state, rvalue.left))
        return state.builder.zext(answer, codegen.types.ll_type(BuiltinType.BOOL))
    ty = _scalar_type(state, rvalue, type_of_operand(state, rvalue.left))
    _scalar_type(state, rvalue, type_of_operand(state, rvalue.right))
    left = operand_value(state, rvalue.left)
    right = operand_value(state, rvalue.right)
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


def _is_string_relation(state: FunctionState, rvalue: r.Binary) -> bool:
    """A comparison of two strings."""
    return (rvalue.op in COMPARISON_OPS
            and type_of_operand(state, rvalue.left) == BuiltinType.STRING
            and type_of_operand(state, rvalue.right) == BuiltinType.STRING)


def _is_contract_relation(state: FunctionState, rvalue: r.Binary) -> bool:
    """A comparison of two enums of one type, which reads the derived or implemented contract."""
    left = type_of_operand(state, rvalue.left)
    return (rvalue.op in COMPARISON_OPS and isinstance(left, EnumType)
            and left == type_of_operand(state, rvalue.right))


def _unary(state: FunctionState, rvalue: r.Unary) -> ir.Value:
    _scalar_type(state, rvalue, type_of_operand(state, rvalue.operand))
    return emit_unary_value(state.codegen, rvalue.op, operand_value(state, rvalue.operand),
                            to_i1=False)


def _cast(state: FunctionState, rvalue: r.Cast) -> ir.Value:
    source = _scalar_type(state, rvalue, type_of_operand(state, rvalue.operand))
    _scalar_type(state, rvalue, rvalue.target)
    return emit_value_cast(state.codegen, operand_value(state, rvalue.operand), source,
                           rvalue.target)


def _scalar_type(state: FunctionState, rvalue: r.Rvalue, ty: Type) -> Type:
    """`ty` when it is an integer, a float or a `bool`; else the rvalue stops."""
    if ty != BuiltinType.BOOL and not is_numeric_type(ty):
        state.stop(rvalue)
    return ty


def _aggregate(state: FunctionState, rvalue: r.Aggregate) -> ir.Value:
    """An enum variant. Any other kind of aggregate stops with its class."""
    kind = rvalue.kind
    if not isinstance(kind, r.AggVariant):
        state.stop(kind)
    enum_type, info = checked_variant(kind.ty, kind.variant,
                                      [type_of_operand(state, part) for part in rvalue.parts],
                                      state.fault)
    values = [operand_value(state, part) for part in rvalue.parts]
    return emit_enum_variant_value(state.codegen, enum_type, info, values)


def _discriminant(state: FunctionState, rvalue: r.Discriminant) -> ir.Value:
    """The tag of the enum in a place, as an `i32`."""
    enum_of(type_of_place(state, rvalue.place), state.fault)
    value = state.builder.load(place_address(state, rvalue.place))
    return enum_utils.extract_enum_tag(state.codegen, value)


_RVALUES: dict[type, Callable[[FunctionState, Any], ir.Value]] = {
    r.Use: _use,
    r.Binary: _binary,
    r.Unary: _unary,
    r.Cast: _cast,
    r.Aggregate: _aggregate,
    r.Discriminant: _discriminant,
}
