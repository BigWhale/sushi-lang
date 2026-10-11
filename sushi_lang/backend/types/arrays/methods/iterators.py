"""The `iter()` producer of a fixed array and of a dynamic array (#1259).

The cursor is the element count and the element data; `backend/runtime/iterators.py`
makes the value of it, by the state the borrow pass chose for the call.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.backend import gep_utils
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_fixed_array_iter(codegen: 'LLVMCodegen', call: MethodCall, array_slot: ir.Value,
                           receiver_type: ir.ArrayType,
                           element_semantic_type: 'Type') -> ir.Value:
    """`a.iter()` on a `T[N]`: the receiver arrives as an address.

    The receiver arrives as an address from `as_fixed_array_address`, and the element type
    from the receiver's own semantic type (#480).
    """
    from sushi_lang.backend.runtime.iterators import (
        OwnedContainer, array_source, emit_cursor_iterator)
    from sushi_lang.semantics.typesys import ArrayType

    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    count = receiver_type.count
    length = ir.Constant(codegen.types.i32, count)
    zero = ir.Constant(codegen.types.i32, 0)
    data = gep_utils.gep_fixed_array_element(codegen, array_slot, zero)

    def slots(codegen: 'LLVMCodegen', held: ir.Value) -> tuple[ir.Value, ir.Value]:
        return length, gep_utils.gep_fixed_array_element(codegen, held, zero)

    def owned() -> OwnedContainer:
        from sushi_lang.backend.expressions.calls.dispatcher import consume_receiver
        value = consume_receiver(codegen, call, codegen.builder.load(array_slot,
                                                                     name="array"))
        return OwnedContainer(value=value,
                              semantic_type=ArrayType(base_type=element_semantic_type,
                                                      size=count),
                              slots=slots)

    return emit_cursor_iterator(codegen, call, array_source(codegen, element_semantic_type),
                                length, data, owned)


def emit_dynamic_array_iter(codegen: 'LLVMCodegen', call: MethodCall, receiver_value: ir.Value,
                             element_semantic_type: 'Type') -> ir.Value:
    """`a.iter()` on a `T[]`: the receiver arrives as the address of its descriptor.

    The element type arrives with the receiver, so a field, a `from()` temporary and a
    chained call result all answer (#482).
    """
    from sushi_lang.backend.runtime.iterators import (
        OwnedContainer, array_source, emit_cursor_iterator)
    from sushi_lang.semantics.typesys import DynamicArrayType

    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    length, data = descriptor_slots(codegen, receiver_value)

    def owned() -> OwnedContainer:
        from sushi_lang.backend.expressions.calls.dispatcher import consume_receiver
        value = consume_receiver(codegen, call, codegen.builder.load(receiver_value,
                                                                     name="array"))
        return OwnedContainer(value=value,
                              semantic_type=DynamicArrayType(base_type=element_semantic_type),
                              slots=descriptor_slots)

    return emit_cursor_iterator(codegen, call, array_source(codegen, element_semantic_type),
                                length, data, owned)


def descriptor_slots(codegen: 'LLVMCodegen', descriptor: ir.Value) -> tuple[ir.Value, ir.Value]:
    """The length and the data of a `{i32 len, i32 cap, T* data}` at an address."""
    b = codegen.builder
    length = b.load(gep_utils.gep_dynamic_array_len(codegen, descriptor), name="length")
    data = b.load(gep_utils.gep_dynamic_array_data(codegen, descriptor), name="data")
    return length, data
