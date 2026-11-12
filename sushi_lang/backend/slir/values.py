"""Places, operands, constants and rvalues of SLIR as LLVM values (docs/design/ir.md 8.4, 8.5, 8.7).

An operator calls the emitter of the old backend that takes LLVM values (ruling Q7), so the
integer and the float rules stay in one place, and the backend folds nothing
(`semantics/integer_ops.py` is the one compile-time home). An rvalue over a type that is
not an integer, a float, a `bool` or an enum comparison is an internal error.

An enum is `{i32 tag, [K x i64] data}`. The tag, a variant and the address of a payload
field come from the one layout authority (`TypeSizing.payload_field_offsets`, read through
`enum_utils` and the enum emitter), at the semantic type of the place, never from a layout
match. A struct and a tuple are an LLVM struct of their fields in the declaration order: a
field is a GEP of its index, and a value is built by the struct emitter. An element, a
length and a slice of an array or a string go through the index family
(`types/arrays/indexing.py`), and an array value through the run family
(`types/arrays/runs.py`), each at the semantic type of its place. An `Aggregate`, as
an rvalue and as a constant, goes through one check against the semantic types of its
parts (`checked_variant`, `checked_struct`). The rvalue calls it and then the enum or the
struct emitter; the constant goes through the one converter of an aggregate constant
(`constant_initializer`), which runs the check itself and which the global of a `const`
reads too.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Callable

from llvmlite import ir

from sushi_lang.backend import enum_utils
from sushi_lang.backend.constants.enum_initializers import (
    checked_struct, checked_variant, constant_initializer, enum_of, scalar_initializer,
    semantic_type_of, variant_of,
)
from sushi_lang.backend.expressions.casts import emit_value_cast
from sushi_lang.backend.expressions.enums import emit_enum_variant_into, emit_enum_variant_value
from sushi_lang.backend.runtime.closures import closure_constant
from sushi_lang.backend.slir.closures import emit_closure_value
from sushi_lang.backend.gep_utils import gep_struct_field
from sushi_lang.backend.generics.list.types import extract_element_type
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.ownership import copy_out, copy_out_into
from sushi_lang.backend.expressions.operators import (
    emit_arithmetic, emit_bitwise, emit_contract_relation, emit_number_comparison, emit_string_relation,
    emit_unary_value, emit_xor,
)
from sushi_lang.backend.slir.foreign import extern_variable, read_extern_variable
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.types.arrays.indexing import array_length, element_address, slice_value
from sushi_lang.backend.types.arrays.runs import (
    emit_array_value, emit_aggregate_copy, emit_fixed_array_into, moves_by_address)
from sushi_lang.backend.types.structs import emit_struct_into, emit_struct_value
from sushi_lang.semantics.operators import ARITHMETIC_OPS, BITWISE_OPS, COMPARISON_OPS, SHIFT_OPS
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.type_predicates import is_instance_of, is_numeric_type
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, PointerType, ReferenceType,
    StructType, Type,
)


def place_address(state: FunctionState, place: p.Place) -> ir.Value:
    """The address of a place: the storage of a local, the global of a `var`, a payload
    field of a variant, a field of a struct or a tuple, an element of an array or a
    string, or the pointee of a pointer (the payload of an `Own@(T)`)."""
    match place:
        case p.Local(local):
            return state.storage_of(local)
        case p.Global(symbol):
            return global_address(state, symbol)
        case p.Field(p.Downcast(base, variant), index):
            types = _payload_types(state, base, variant, index)
            offsets = state.codegen.types.payload_field_offsets(types)
            data = enum_utils.get_data_ptr(state.codegen, place_address(state, base))
            return enum_utils.variant_field_address(state.codegen, data, types[index],
                                                    offsets[index])
        case p.Field(base, index):
            _field_type(state, base, index)
            return gep_struct_field(state.codegen, place_address(state, base), index,
                                    builder=state.builder)
        case p.Index(base, index):
            return element_address(state.codegen, place_address(state, base),
                                   type_of_place(state, base), operand_value(state, index))
        case p.Deref(base):
            _pointee_type(state, base)
            return state.builder.load(place_address(state, base))
    state.unhandled(place)


def global_address(state: FunctionState, symbol: str) -> ir.Value:
    """The global of a `var` in the module. The declaration round makes it in every module:
    the module of its unit defines it, and each other module declares it external (ir.md
    8.2). A symbol that the module does not hold is an internal error."""
    if extern_variable(state, symbol) is not None:
        state.fault(f"the C global '{symbol}' is used by its address")
    found = state.codegen.module.globals.get(symbol)
    if not isinstance(found, ir.GlobalVariable):
        state.fault(f"the module holds no global '{symbol}'")
    return found


def type_of_place(state: FunctionState, place: p.Place) -> Type:
    match place:
        case p.Local(local):
            return state.type_of(local)
        case p.Global(_, ty):
            return ty
        case p.Field(p.Downcast(base, variant), index):
            return _payload_types(state, base, variant, index)[index]
        case p.Field(base, index):
            return _field_type(state, base, index)
        case p.Index(base, _):
            return _element_type(state, base)
        case p.Deref(base):
            return _pointee_type(state, base)
    state.unhandled(place)


def _pointee_type(state: FunctionState, base: p.Place) -> Type:
    """The type that the pointer in `base` points to: the payload of an `Own@(T)`."""
    ty = type_of_place(state, base)
    if not isinstance(ty, PointerType):
        state.fault(f"{base} of {ty} is no pointer")
    return ty.pointee_type


def _element_type(state: FunctionState, base: p.Place) -> Type:
    """The type of an element of the array or the `List@(T)`, or of a byte of the string, in
    `base`."""
    ty = type_of_place(state, base)
    if ty == BuiltinType.STRING:
        return BuiltinType.U8
    if isinstance(ty, StructType) and is_instance_of(ty, "List"):
        return extract_element_type(ty, state.codegen)
    if not isinstance(ty, (ArrayType, DynamicArrayType)):
        state.fault(f"{base} of {ty} has no element")
    return ty.base_type


def _field_type(state: FunctionState, base: p.Place, index: int) -> Type:
    """The type of the field `index` of the struct or the tuple in `base`."""
    ty = type_of_place(state, base)
    if not isinstance(ty, StructType) or not 0 <= index < len(ty.fields):
        state.fault(f"{base} of {ty} has no field {index}")
    return ty.fields[index][1]


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
    SLIR carry the ownership, so `Copy` and `Move` read alike. A `Copy` of a C global is
    the read of `foreign.py`."""
    match operand:
        case p.Const(constant):
            return constant_value(state, constant)
        case p.Copy(p.Global(symbol)) if extern_variable(state, symbol) is not None:
            return read_extern_variable(state, symbol)
        case p.Copy(place) | p.Move(place):
            return state.builder.load(place_address(state, place))


def operand_address(state: FunctionState, operand: p.Operand) -> ir.Value:
    """The address of an operand: the storage of its place, or a new entry-block slot that
    holds its constant."""
    match operand:
        case p.Copy(place) | p.Move(place):
            return place_address(state, place)
        case p.Const(constant):
            slot = entry_alloca(state.builder,
                                state.codegen.types.ll_type(type_of_constant(state, constant)),
                                "arg")
            state.builder.store(constant_value(state, constant), slot)
            return slot


def type_of_operand(state: FunctionState, operand: p.Operand) -> Type:
    match operand:
        case p.Const(constant):
            return type_of_constant(state, constant)
        case p.Copy(place) | p.Move(place):
            return type_of_place(state, place)


def constant_value(state: FunctionState, constant: k.Constant) -> ir.Value:
    """An LLVM value of a constant. A string literal comes from the string constant seam,
    and it owns no heap (its owned flag is 0). A struct, a tuple and a variant go through the
    one converter of an aggregate constant."""
    codegen = state.codegen
    if isinstance(constant, k.Str):
        return codegen.runtime.strings.emit_string_literal(
            constant.value.decode("utf-8", "surrogatepass"))
    if isinstance(constant, k.Aggregate):
        return constant_initializer(constant, codegen.types, _literal_constant(state), "",
                                    span=state.span, filename=state.filename,
                                    finish_function=lambda symbol: closure_constant(codegen, symbol))
    if isinstance(constant, k.FnRef):
        state.unhandled(constant)
    return scalar_initializer(constant, codegen.types, state.fault)


def _literal_constant(state: FunctionState) -> Callable[[str, str], ir.Constant]:
    """The string finisher of a constant payload: the constant fat pointer of a literal."""
    strings = state.codegen.runtime.strings
    return lambda text, _name: strings.string_literal_constant(text)


def type_of_constant(state: FunctionState, constant: k.Constant) -> Type:
    if isinstance(constant, k.FnRef):
        state.unhandled(constant)
    return semantic_type_of(constant, state.fault)


def assign(state: FunctionState, place: p.Place, rvalue: r.Rvalue) -> None:
    """The value of `rvalue` into `place`. A `T[N]` literal is built in the place, and a
    `T[N]` that moves by address is copied, or cloned, from the address of its source
    (ir.md 8.7)."""
    if (isinstance(rvalue, r.Aggregate) and isinstance(rvalue.kind, r.AggArray)
            and isinstance(rvalue.kind.ty, ArrayType)):
        _fixed_array(state, place, rvalue.kind.runs, rvalue.kind.ty, rvalue.parts)
        return
    if (isinstance(rvalue, r.Aggregate)
            and isinstance(rvalue.kind, (r.AggVariant, r.AggStruct, r.AggTuple))
            and moves_by_address(state.codegen.types.ll_type(type_of_place(state, place)))):
        _aggregate_into(state, place, rvalue)
        return
    if isinstance(rvalue, (r.Use, r.Clone)) and isinstance(rvalue.operand, (p.Copy, p.Move)):
        source = rvalue.operand.place
        source_type = type_of_place(state, source)
        llvm_type = state.codegen.types.ll_type(source_type)
        if moves_by_address(llvm_type) and not (
                isinstance(source, p.Global) and extern_variable(state, source.symbol)):
            if isinstance(rvalue, r.Clone):
                copy_out_into(state.codegen, place_address(state, place),
                              place_address(state, source), source_type)
            else:
                emit_aggregate_copy(state.codegen, place_address(state, place),
                                      place_address(state, source), llvm_type)
            return
    value = rvalue_value(state, rvalue)
    state.builder.store(value, place_address(state, place))


def _fixed_array(state: FunctionState, place: p.Place, runs: tuple[r.ArrayRun, ...],
                 ty: ArrayType, operands: tuple[p.Operand, ...]) -> None:
    """A part that moves by address is read while the slots fill, and a reference can make
    any place an alias of `place`. So the array is built in `place` only when no part moves
    by address; else it is built in a fresh slot and then copied into `place`."""
    parts = _array_parts(state, ty, operands)
    llvm_type = state.codegen.types.ll_type(ty)
    if not moves_by_address(state.codegen.types.ll_type(ty.base_type)):
        emit_fixed_array_into(state.codegen, ty, runs, parts, place_address(state, place))
        return
    slot = entry_alloca(state.builder, llvm_type, "array_literal")
    emit_fixed_array_into(state.codegen, ty, runs, parts, slot)
    emit_aggregate_copy(state.codegen, place_address(state, place), slot, llvm_type)


def _aggregate_into(state: FunctionState, place: p.Place, rvalue: r.Aggregate) -> None:
    """A struct, a tuple or a variant that moves by address, built field by field. A part
    that moves by address is read while the fields fill, and a reference can make any place
    an alias of `place`. So the value is built in `place` only when no part moves by
    address; else it is built in a fresh slot and then copied into `place`."""
    codegen = state.codegen
    kind = rvalue.kind
    types = [type_of_operand(state, part) for part in rvalue.parts]
    if isinstance(kind, r.AggVariant):
        enum_type, info = checked_variant(kind.ty, kind.variant, types, state.fault)
        payload = list(info.associated_types)
        build = partial(emit_enum_variant_into, codegen, enum_type, info)
        llvm_type = codegen.types.ll_type(enum_type)
    else:
        struct_type = checked_struct(kind.ty, types, state.fault)
        payload = [field_ty for _name, field_ty in struct_type.fields]
        build = partial(emit_struct_into, codegen, struct_type)
        llvm_type = codegen.types.ll_type(struct_type)
    by_address = [moves_by_address(codegen.types.ll_type(ty)) for ty in payload]
    parts = [operand_address(state, part) if moved else _part(state, part, ty)
             for part, ty, moved in zip(rvalue.parts, payload, by_address, strict=True)]
    if not any(by_address):
        build(parts, place_address(state, place))
        return
    slot = entry_alloca(state.builder, llvm_type, "aggregate_literal")
    build(parts, slot)
    emit_aggregate_copy(codegen, place_address(state, place), slot, llvm_type)


def _array_parts(state: FunctionState, ty: ArrayType | DynamicArrayType,
                 parts: tuple[p.Operand, ...]) -> list[ir.Value]:
    """The value of each part, and the address of a part of an element type that moves by
    address."""
    by_address = moves_by_address(state.codegen.types.ll_type(ty.base_type))
    return [operand_address(state, part)
            if by_address and type_of_operand(state, part) == ty.base_type
            else operand_value(state, part) for part in parts]


def rvalue_value(state: FunctionState, rvalue: r.Rvalue) -> ir.Value:
    """The value of an rvalue, from the one table of the rvalue classes."""
    emit = _RVALUES.get(type(rvalue))
    if emit is None:
        state.unhandled(rvalue)
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
        answer = emit_contract_relation(codegen, rvalue.op,
                                        by_address_or_value(state, rvalue.left),
                                        by_address_or_value(state, rvalue.right),
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
    """A comparison of two enums or two structs of one type (a tuple is a struct), which
    reads the derived or implemented contract."""
    left = type_of_operand(state, rvalue.left)
    return (rvalue.op in COMPARISON_OPS and isinstance(left, (EnumType, StructType))
            and left == type_of_operand(state, rvalue.right))


def _unary(state: FunctionState, rvalue: r.Unary) -> ir.Value:
    _scalar_type(state, rvalue, type_of_operand(state, rvalue.operand))
    return emit_unary_value(state.codegen, rvalue.op, operand_value(state, rvalue.operand),
                            to_i1=False)


def _cast(state: FunctionState, rvalue: r.Cast) -> ir.Value:
    """A numeric cast, or the identity cast of a value of any type, which is the value."""
    if type_of_operand(state, rvalue.operand) == rvalue.target:
        return operand_value(state, rvalue.operand)
    source = _scalar_type(state, rvalue, type_of_operand(state, rvalue.operand))
    _scalar_type(state, rvalue, rvalue.target)
    return emit_value_cast(state.codegen, operand_value(state, rvalue.operand), source,
                           rvalue.target)


def _scalar_type(state: FunctionState, rvalue: r.Rvalue, ty: Type) -> Type:
    """`ty` when it is an integer, a float or a `bool`; else an internal error."""
    if ty != BuiltinType.BOOL and not is_numeric_type(ty):
        state.unhandled(rvalue)
    return ty


def _aggregate(state: FunctionState, rvalue: r.Aggregate) -> ir.Value:
    """An enum variant, a struct, a tuple or an array. Any other kind of aggregate is an
    internal error."""
    kind = rvalue.kind
    if isinstance(kind, r.AggArray):
        if not isinstance(kind.ty, DynamicArrayType):
            state.fault(f"an array value of {kind.ty} outside an assignment")
        return emit_array_value(state.codegen, kind.ty, kind.runs,
                                _array_parts(state, kind.ty, rvalue.parts))
    if isinstance(kind, r.AggClosure):
        return emit_closure_value(state, rvalue,
                                  [by_address_or_value(state, part)
                                   for part in rvalue.parts[1:]])
    if isinstance(kind, r.AggIterator):
        from sushi_lang.backend.slir.iterators import emit_iterator_value
        return emit_iterator_value(state, rvalue, kind)
    if not isinstance(kind, (r.AggVariant, r.AggStruct, r.AggTuple)):
        state.unhandled(kind)
    parts = [type_of_operand(state, part) for part in rvalue.parts]
    if isinstance(kind, r.AggVariant):
        enum_type, info = checked_variant(kind.ty, kind.variant, parts, state.fault)
        values = [_part(state, part, payload)
                  for part, payload in zip(rvalue.parts, info.associated_types, strict=True)]
        return emit_enum_variant_value(state.codegen, enum_type, info, values)
    struct_type = checked_struct(kind.ty, parts, state.fault)
    fields = [field_ty for _name, field_ty in struct_type.fields]
    return emit_struct_value(state.codegen, struct_type,
                             [_part(state, part, field_ty)
                              for part, field_ty in zip(rvalue.parts, fields, strict=True)])


def by_address_or_value(state: FunctionState, part: p.Operand) -> ir.Value:
    """The address of a part that moves by address, else its value."""
    if moves_by_address(state.codegen.types.ll_type(type_of_operand(state, part))):
        return operand_address(state, part)
    return operand_value(state, part)


def _part(state: FunctionState, part: p.Operand, ty: Type) -> ir.Value:
    """The value of a part of a struct or of a variant: the address of its place when the
    field or the payload has a reference type (ir.md 8.7)."""
    if not isinstance(ty, ReferenceType):
        return operand_value(state, part)
    if not isinstance(part, (p.Copy, p.Move)):
        state.fault(f"a part of a reference field that reads no place: {part}")
    return place_address(state, part.place)


def _clone(state: FunctionState, rvalue: r.Clone) -> ir.Value:
    """The one deep copy (ir.md 8.7): `copy_out` of the operand at its semantic type."""
    return copy_out(state.codegen, operand_value(state, rvalue.operand),
                    type_of_operand(state, rvalue.operand))


def _discriminant(state: FunctionState, rvalue: r.Discriminant) -> ir.Value:
    """The tag of the enum in a place, as an `i32`."""
    enum_of(type_of_place(state, rvalue.place), state.fault)
    return state.builder.load(enum_utils.get_tag_ptr(state.codegen,
                                                     place_address(state, rvalue.place)),
                              name="tag")


def _len(state: FunctionState, rvalue: r.Len) -> ir.Value:
    return array_length(state.codegen, place_address(state, rvalue.place),
                        type_of_place(state, rvalue.place))


def _slice(state: FunctionState, rvalue: r.Slice) -> ir.Value:
    return slice_value(state.codegen, place_address(state, rvalue.place),
                       type_of_place(state, rvalue.place), operand_value(state, rvalue.start),
                       operand_value(state, rvalue.end))


_RVALUES: dict[type, Callable[[FunctionState, Any], ir.Value]] = {
    r.Use: _use,
    r.Binary: _binary,
    r.Unary: _unary,
    r.Cast: _cast,
    r.Aggregate: _aggregate,
    r.Discriminant: _discriminant,
    r.Clone: _clone,
    r.Len: _len,
    r.Slice: _slice,
}
