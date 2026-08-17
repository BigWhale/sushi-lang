"""Array iterator emission for foreach loops."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.backend import gep_utils
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_fixed_array_iter(codegen: 'LLVMCodegen', call: MethodCall, receiver_value: ir.Value,
                           receiver_type: ir.ArrayType, to_i1: bool) -> ir.Value:
    """Emit LLVM IR for fixed array iter() method."""
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    array_length = receiver_type.count

    if isinstance(call.receiver, Name):
        # Local alloca, or the global backing an array constant (#248). The iterator
        # struct holds a data pointer, so `foreach(p in PRIMES.iter())` reads straight
        # out of .rodata -- no copy.
        from sushi_lang.backend.expressions.names import resolve_name_slot
        array_slot = resolve_name_slot(codegen, call.receiver.id)
        if array_slot is None:
            raise_internal_error("CE0055", name=call.receiver.id)
    else:
        array_slot = codegen.builder.alloca(receiver_type)
        codegen.builder.store(receiver_value, array_slot)

    zero = ir.Constant(codegen.types.i32, 0)
    data_ptr = gep_utils.gep_fixed_array_element(codegen, array_slot, zero)

    from sushi_lang.semantics.typesys import IteratorType, ArrayType as SushiArrayType

    # Determine element type from semantic information (a constant's type lives in the
    # const table, not in either locals table -- #248)
    if isinstance(call.receiver, Name):
        from sushi_lang.backend.expressions.names import resolve_name_semantic_type
        semantic_type = resolve_name_semantic_type(codegen, call.receiver.id)
        if isinstance(semantic_type, SushiArrayType):
            element_semantic_type = semantic_type.base_type
        else:
            raise_internal_error("CE0041", type=type(semantic_type).__name__)
    else:
        raise_internal_error("CE0072", operation="iter() on a complex array expression")

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type)

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, array_length), length_ptr)

    data_ptr_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr)

    return codegen.builder.load(iterator_slot)


def emit_dynamic_array_iter(codegen: 'LLVMCodegen', call: MethodCall, receiver_value: ir.Value,
                             receiver_type: ir.LiteralStructType, to_i1: bool) -> ir.Value:
    """Emit LLVM IR for dynamic array iter() method."""
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    len_ptr = gep_utils.gep_dynamic_array_len(codegen, receiver_value)
    length = codegen.builder.load(len_ptr)

    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, receiver_value)
    data_ptr = codegen.builder.load(data_ptr_ptr)

    from sushi_lang.semantics.typesys import IteratorType, DynamicArrayType as SushiDynamicArrayType

    from sushi_lang.semantics.typesys import ReferenceType
    if isinstance(call.receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(call.receiver.id)
        # A borrowed array (peek/poke T[]) surfaces as a ReferenceType; unwrap it
        # to the referenced DynamicArrayType. The receiver_value is already the
        # array-struct pointer (emit_receiver_value loads the reference slot).
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type
        if isinstance(semantic_type, SushiDynamicArrayType):
            element_semantic_type = semantic_type.base_type
        else:
            raise_internal_error("CE0042", type=type(semantic_type).__name__)
    else:
        raise_internal_error("CE0072", operation="iter() on a complex dynamic array expression")

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type)

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(length, length_ptr)

    data_ptr_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr_out)

    return codegen.builder.load(iterator_slot)


