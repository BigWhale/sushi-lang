"""The equality and the order of ONE value, by its semantic type.

`==` and `!=` on a struct or an enum, `contains` and `index_of`, the HashMap probe, a
field of a derived equality and an explicit `a.eq(b)` all ask `emit_value_eq`, so a
type compares the same in every position. `emit_value_compare` is the same seam for
`Ord` (`<`, `compare`). The order is the method-resolution order: an implementation of
the contract is the sanctioned override and wins, then the derived function, then the
primitive rule. An overridden type is terminal: its fields are not read.

A composite type gets ONE out-of-line `linkonce_odr` function per contract, cached
before its body is emitted, so a type that reaches itself through `Own@(T)` or
`List@(T)` compares by a call of the function it is in.

A float HELD in a type compares by a TOTAL rule, so that `compare == 0` is exactly
`eq`: `-0.0 == 0.0`, every NaN equals every other NaN, and a NaN orders after every
number. A bare `==` on two floats is IEEE and never reaches here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import llvmlite.ir as ir

from sushi_lang.backend import enum_utils
from sushi_lang.backend.generics.container_walk import emit_container_search
from sushi_lang.backend.generics.list.types import get_list_data_ptr, get_list_len_ptr
from sushi_lang.backend.lifecycle import lifecycle_symbol
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.contracts import CONTRACT_METHOD, EQ, ORD
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, StructType, Type)

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


_SIGNED = frozenset({BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64})
_FLOATS = frozenset({BuiltinType.F32, BuiltinType.F64})


# --- the seams ---------------------------------------------------------------------

def emit_value_eq(codegen: 'LLVMCodegen', a: ir.Value, b: ir.Value, ty: Type) -> ir.Value:
    """The i1 answer to "does `a` equal `b`", two loaded values of `ty`."""
    override = contract_override(codegen, ty, EQ)
    if override is not None:
        return codegen.utils.as_i1(_call_override(codegen, override, [a, b]))
    if isinstance(ty, BuiltinType):
        return _primitive_eq(codegen, a, b, ty)
    return codegen.builder.call(_contract_func(codegen, ty, EQ), [a, b])


def emit_value_compare(codegen: 'LLVMCodegen', a: ir.Value, b: ir.Value,
                       ty: Type) -> ir.Value:
    """The i32 order of `a` against `b`: negative, zero or positive."""
    override = contract_override(codegen, ty, ORD)
    if override is not None:
        return _call_override(codegen, override, [a, b])
    if isinstance(ty, BuiltinType):
        return _primitive_compare(codegen, a, b, ty)
    return codegen.builder.call(_contract_func(codegen, ty, ORD), [a, b])


def contract_override(codegen: 'LLVMCodegen', ty: Type, contract: str) -> Optional[ir.Function]:
    """The function of the type's implementation of `contract`, or None.

    Read by the PERK name: a user perk that happens to provide `compare` is not the
    order `<` reads.
    """
    from sushi_lang.semantics.passes.collect.perks import _get_type_name
    type_name = _get_type_name(ty)
    if type_name is None or not codegen.perk_impl_table.implements(type_name, contract):
        return None
    from sushi_lang.semantics.library_templates import impl_method_symbol
    method = CONTRACT_METHOD[contract]
    llvm_fn = codegen.funcs.get(impl_method_symbol(str(ty), method))
    if llvm_fn is None:
        raise_internal_error("CE0024", method=method, type=str(ty))
    return llvm_fn


def _call_override(codegen: 'LLVMCodegen', fn: ir.Function, values: list) -> ir.Value:
    """Call an implementation, spilling a value whose parameter takes an address."""
    builder = codegen.builder
    args = []
    for value, param in zip(values, fn.args, strict=True):
        param_type = param.type
        if isinstance(param_type, ir.PointerType) and param_type.pointee == value.type:
            slot = entry_alloca(builder, value.type, name="contract_arg")
            builder.store(value, slot)
            args.append(slot)
        else:
            args.append(codegen.utils.cast_for_param(value, param_type))
    return builder.call(fn, args)


def load_operand(codegen: 'LLVMCodegen', value: ir.Value, ty: Type) -> ir.Value:
    """A borrowed operand arrives as the address of its value; the seams read values."""
    llvm_type = codegen.types.ll_type(ty)
    if isinstance(value.type, ir.PointerType) and value.type.pointee == llvm_type:
        return codegen.builder.load(value, name="contract_operand")
    return value


# --- primitives --------------------------------------------------------------------

def _is_nan(builder: ir.IRBuilder, x: ir.Value) -> ir.Value:
    return builder.fcmp_unordered("uno", x, x, name="is_nan")


def _primitive_eq(codegen: 'LLVMCodegen', a: ir.Value, b: ir.Value,
                  ty: BuiltinType) -> ir.Value:
    builder = codegen.builder
    if ty == BuiltinType.STRING:
        return codegen.runtime.strings.emit_string_comparison("==", a, b)
    if ty == BuiltinType.BLANK:
        return ir.Constant(ir.IntType(1), 1)
    if ty in _FLOATS:
        both_nan = builder.and_(_is_nan(builder, a), _is_nan(builder, b))
        return builder.or_(builder.fcmp_ordered("==", a, b), both_nan, name="float_eq")
    if ty == BuiltinType.BOOL:
        return builder.icmp_unsigned("==", codegen.utils.as_i1(a), codegen.utils.as_i1(b))
    return builder.icmp_unsigned("==", a, b, name="int_eq")


def _three_way(codegen: 'LLVMCodegen', less: ir.Value, greater: ir.Value) -> ir.Value:
    """-1, 0 or 1 from the two strict answers."""
    i32 = codegen.types.i32
    builder = codegen.builder
    high = builder.select(greater, ir.Constant(i32, 1), ir.Constant(i32, 0))
    return builder.select(less, ir.Constant(i32, -1), high, name="order")


def _primitive_compare(codegen: 'LLVMCodegen', a: ir.Value, b: ir.Value,
                       ty: BuiltinType) -> ir.Value:
    builder = codegen.builder
    i32 = codegen.types.i32
    if ty == BuiltinType.STRING:
        diff = codegen.runtime.strings._emit_string_three_way(a, b)
        zero = ir.Constant(i32, 0)
        return _three_way(codegen, builder.icmp_signed("<", diff, zero),
                          builder.icmp_signed(">", diff, zero))
    if ty == BuiltinType.BLANK:
        return ir.Constant(i32, 0)
    if ty in _FLOATS:
        a_nan, b_nan = _is_nan(builder, a), _is_nan(builder, b)
        # A NaN orders after every number; two NaNs are equal.
        less = builder.or_(builder.fcmp_ordered("<", a, b),
                           builder.and_(builder.not_(a_nan), b_nan))
        greater = builder.or_(builder.fcmp_ordered(">", a, b),
                              builder.and_(a_nan, builder.not_(b_nan)))
        return _three_way(codegen, less, greater)
    if ty == BuiltinType.BOOL:
        x, y = codegen.utils.as_i1(a), codegen.utils.as_i1(b)
        return _three_way(codegen, builder.icmp_unsigned("<", x, y),
                          builder.icmp_unsigned(">", x, y))
    if ty in _SIGNED:
        return _three_way(codegen, builder.icmp_signed("<", a, b),
                          builder.icmp_signed(">", a, b))
    return _three_way(codegen, builder.icmp_unsigned("<", a, b),
                      builder.icmp_unsigned(">", a, b))


# --- the out-of-line functions -----------------------------------------------------

_PREFIX = {EQ: "__sushi_eq_", ORD: "__sushi_cmp_"}


def _contract_func(codegen: 'LLVMCodegen', ty: Type, contract: str) -> ir.Function:
    """Get (or lazily emit) the derived function of `contract` for one composite type.

    The function is cached BEFORE its body is emitted, so a nested value of the same
    type is a call of this function. The body is emitted mid-emission of another
    function, so the builder and the current function are swapped and restored, as
    `get_or_emit_lifecycle_func` does (#257).
    """
    symbol = lifecycle_symbol(_PREFIX[contract], ty)
    cached = codegen._contract_funcs.get(symbol)
    if cached is not None:
        return cached

    llvm_type = codegen.types.ll_type(ty)
    result_type = ir.IntType(1) if contract == EQ else codegen.types.i32
    fn = ir.Function(codegen.module, ir.FunctionType(result_type, [llvm_type, llvm_type]),
                     name=symbol)
    fn.linkage = "linkonce_odr"
    codegen._contract_funcs[symbol] = fn

    saved_builder, saved_func = codegen.builder, codegen.func
    codegen.builder = ir.IRBuilder(fn.append_basic_block(name="entry"))
    codegen.func = fn
    try:
        _emit_body(codegen, fn, ty, contract)
    finally:
        codegen.builder, codegen.func = saved_builder, saved_func
    return fn


class _Steps:
    """The body of one contract function: a run of steps, each of which may decide.

    An equality step answers "equal so far"; the first false returns 0. An order step
    answers an i32; the first non-zero returns it. After the last step the answer is
    "equal" (1, or 0 for an order).
    """

    def __init__(self, codegen: 'LLVMCodegen', contract: str) -> None:
        self.codegen = codegen
        self.contract = contract

    def step(self, a: ir.Value, b: ir.Value, ty: Type) -> None:
        """Compare one pair of held values, and return at once when it decides."""
        if self.contract == EQ:
            self.decide_eq(emit_value_eq(self.codegen, a, b, ty))
        else:
            self.decide_order(emit_value_compare(self.codegen, a, b, ty))

    def decide_eq(self, equal: ir.Value) -> None:
        builder = self.codegen.builder
        unequal = builder.append_basic_block(name="unequal")
        next_block = builder.append_basic_block(name="next")
        builder.cbranch(equal, next_block, unequal)
        builder.position_at_end(unequal)
        builder.ret(ir.Constant(ir.IntType(1), 0))
        builder.position_at_end(next_block)

    def decide_order(self, order: ir.Value) -> None:
        builder = self.codegen.builder
        decided = builder.append_basic_block(name="decided")
        next_block = builder.append_basic_block(name="next")
        builder.cbranch(builder.icmp_signed("!=", order, ir.Constant(order.type, 0)),
                        decided, next_block)
        builder.position_at_end(decided)
        builder.ret(order)
        builder.position_at_end(next_block)

    def finish(self) -> None:
        if self.contract == EQ:
            self.codegen.builder.ret(ir.Constant(ir.IntType(1), 1))
        else:
            self.codegen.builder.ret(ir.Constant(self.codegen.types.i32, 0))


def _emit_body(codegen: 'LLVMCodegen', fn: ir.Function, ty: Type, contract: str) -> None:
    a, b = fn.args
    steps = _Steps(codegen, contract)
    if isinstance(ty, EnumType):
        _enum_body(codegen, steps, a, b, ty)
    elif isinstance(ty, StructType) and is_instance_of(ty, "Own"):
        _own_body(codegen, steps, a, b, ty)
    elif isinstance(ty, StructType) and is_instance_of(ty, "List"):
        _sequence_body(codegen, steps, ty.generic_args[0], _list_view(codegen, a),
                       _list_view(codegen, b))
    elif isinstance(ty, StructType):
        for index, (_name, field_type) in enumerate(ty.fields):
            steps.step(codegen.builder.extract_value(a, index),
                       codegen.builder.extract_value(b, index), field_type)
    elif isinstance(ty, DynamicArrayType):
        builder = codegen.builder
        _sequence_body(codegen, steps, ty.base_type,
                       (builder.extract_value(a, 2), builder.extract_value(a, 0)),
                       (builder.extract_value(b, 2), builder.extract_value(b, 0)))
    elif isinstance(ty, ArrayType):
        _sequence_body(codegen, steps, ty.base_type, _fixed_view(codegen, a, ty),
                       _fixed_view(codegen, b, ty))
    else:
        raise_internal_error("CE0052", type=str(ty))
    steps.finish()


def _spill(codegen: 'LLVMCodegen', value: ir.Value, name: str) -> ir.Value:
    slot = entry_alloca(codegen.builder, value.type, name=name)
    codegen.builder.store(value, slot)
    return slot


def _list_view(codegen: 'LLVMCodegen', value: ir.Value) -> tuple:
    """(data pointer, length) of a `List@(T)` value."""
    builder = codegen.builder
    slot = _spill(codegen, value, "list_view")
    return (builder.load(get_list_data_ptr(builder, slot), name="list_data"),
            builder.load(get_list_len_ptr(builder, slot), name="list_len"))


def _fixed_view(codegen: 'LLVMCodegen', value: ir.Value, ty: ArrayType) -> tuple:
    """(data pointer, length) of a fixed array value."""
    zero = ir.Constant(codegen.types.i32, 0)
    slot = _spill(codegen, value, "array_view")
    data = codegen.builder.gep(slot, [zero, zero], name="array_data")
    return data, ir.Constant(codegen.types.i32, ty.size)


def _sequence_body(codegen: 'LLVMCodegen', steps: _Steps, element_type: Type,
                   left: tuple, right: tuple) -> None:
    """Element by element over the common length, then the lengths.

    An equality asks the lengths first, since two sequences of different lengths are
    never equal; an order walks the common prefix, and a prefix is less.
    """
    builder = codegen.builder
    (a_data, a_len), (b_data, b_len) = left, right
    if steps.contract == EQ:
        steps.decide_eq(builder.icmp_signed("==", a_len, b_len))
        count = a_len
    else:
        count = builder.select(builder.icmp_signed("<", a_len, b_len), a_len, b_len,
                               name="common_len")

    order_slot = None
    if steps.contract == ORD:
        order_slot = entry_alloca(builder, codegen.types.i32, name="element_order")

    def differs(element_ptr: ir.Value, index: ir.Value) -> ir.Value:
        x = codegen.builder.load(element_ptr, name="left_element")
        y_ptr = codegen.builder.gep(b_data, [index], name="right_element_ptr")
        y = codegen.builder.load(y_ptr, name="right_element")
        if steps.contract == EQ:
            return codegen.builder.not_(emit_value_eq(codegen, x, y, element_type))
        order = emit_value_compare(codegen, x, y, element_type)
        codegen.builder.store(order, order_slot)
        return codegen.builder.icmp_signed("!=", order, ir.Constant(order.type, 0))

    found, _index = emit_container_search(codegen, a_data, count, differs,
                                          prefix="contract_walk")
    if steps.contract == EQ:
        steps.decide_eq(builder.not_(found))
        return
    decided = builder.append_basic_block(name="element_decided")
    lengths = builder.append_basic_block(name="lengths")
    builder.cbranch(found, decided, lengths)
    builder.position_at_end(decided)
    builder.ret(builder.load(order_slot))
    builder.position_at_end(lengths)
    steps.step(a_len, b_len, BuiltinType.I32)


def _own_body(codegen: 'LLVMCodegen', steps: _Steps, a: ir.Value, b: ir.Value,
              ty: StructType) -> None:
    """An `Own@(T)` compares its payload, never the address."""
    payload_type = ty.generic_args[0]
    builder = codegen.builder
    zero = ir.Constant(codegen.types.i32, 0)

    def payload(value: ir.Value, name: str) -> ir.Value:
        slot = _spill(codegen, value, f"{name}_own")
        pointer = builder.load(builder.gep(slot, [zero, zero]), name=f"{name}_ptr")
        return builder.load(pointer, name=f"{name}_payload")

    steps.step(payload(a, "left"), payload(b, "right"), payload_type)


def _enum_body(codegen: 'LLVMCodegen', steps: _Steps, a: ir.Value, b: ir.Value,
               ty: EnumType) -> None:
    """The variant first, in declaration order; then the payload, left to right."""
    builder = codegen.builder
    a_tag = enum_utils.extract_enum_tag(codegen, a, name="left_tag")
    b_tag = enum_utils.extract_enum_tag(codegen, b, name="right_tag")
    steps.step(a_tag, b_tag, BuiltinType.I32)

    carrying = [(index, variant) for index, variant in enumerate(ty.variants)
                if variant.associated_types]
    if not carrying:
        return

    a_data = enum_utils.get_data_ptr(codegen, _spill(codegen, a, "left_enum"), "left_data")
    b_data = enum_utils.get_data_ptr(codegen, _spill(codegen, b, "right_enum"), "right_data")
    done = builder.append_basic_block(name="payload_done")
    switch = builder.switch(a_tag, done)
    for index, variant in carrying:
        case = builder.append_basic_block(name=f"payload_{variant.name}")
        switch.add_case(ir.Constant(a_tag.type, index), case)
        builder.position_at_end(case)
        lefts = enum_utils.unpack_all_variant_fields(
            codegen, a_data, variant.associated_types, f"left_{variant.name}")
        rights = enum_utils.unpack_all_variant_fields(
            codegen, b_data, variant.associated_types, f"right_{variant.name}")
        for x, y, field_type in zip(lefts, rights, variant.associated_types, strict=True):
            steps.step(x, y, field_type)
        builder.branch(done)
    builder.position_at_end(done)
