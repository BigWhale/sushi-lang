"""
Array iterator emission for foreach loops.

This module handles LLVM IR emission for creating iterators from arrays.
Iterators are used in foreach loops to traverse array elements.
"""
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
    """Emit LLVM IR for fixed array iter() method.

    Creates an iterator struct: {i32 current_index, i32 length, T* data_ptr}
    """
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    # Get array element type and length
    element_type = receiver_type.element
    array_length = receiver_type.count

    # Get pointer to the array (either from slot or allocate)
    if isinstance(call.receiver, Name):
        # Get the array slot directly from memory manager
        array_slot = codegen.memory.find_local_slot(call.receiver.id)
    else:
        # For complex expressions, allocate temporary storage
        array_slot = codegen.builder.alloca(receiver_type)
        codegen.builder.store(receiver_value, array_slot)

    # Get pointer to first element: GEP array_slot, [0, 0]
    zero = ir.Constant(codegen.types.i32, 0)
    data_ptr = gep_utils.gep_fixed_array_element(codegen, array_slot, zero)

    # Create iterator struct: {i32 current_index, i32 length, T* data_ptr}
    # Initialize with: {0, array_length, data_ptr}
    from sushi_lang.semantics.typesys import IteratorType, ArrayType as SushiArrayType

    # Determine element type from semantic information
    if isinstance(call.receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(call.receiver.id)
        if isinstance(semantic_type, SushiArrayType):
            element_semantic_type = semantic_type.base_type
        else:
            raise_internal_error("CE0041", type=type(semantic_type).__name__)
    else:
        raise NotImplementedError("iter() on complex array expressions not fully supported yet")

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    # Allocate and initialize iterator struct
    iterator_slot = codegen.builder.alloca(iterator_struct_type)

    # Set current_index = 0
    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    # Set length = array_length
    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, array_length), length_ptr)

    # Set data_ptr = pointer to first element
    data_ptr_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr)

    # Load and return the iterator struct
    return codegen.builder.load(iterator_slot)


def emit_dynamic_array_iter(codegen: 'LLVMCodegen', call: MethodCall, receiver_value: ir.Value,
                             receiver_type: ir.LiteralStructType, to_i1: bool) -> ir.Value:
    """Emit LLVM IR for dynamic array iter() method.

    Creates an iterator struct: {i32 current_index, i32 length, T* data_ptr}
    """
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    # receiver_value is a pointer to the dynamic array struct {i32 len, i32 cap, T* data}
    zero = ir.Constant(codegen.types.i32, 0)

    # Extract length from dynamic array struct (field 0)
    len_ptr = gep_utils.gep_dynamic_array_len(codegen, receiver_value)
    length = codegen.builder.load(len_ptr)

    # Extract data pointer from dynamic array struct (field 2)
    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, receiver_value)
    data_ptr = codegen.builder.load(data_ptr_ptr)

    # Create iterator struct: {i32 current_index, i32 length, T* data_ptr}
    from sushi_lang.semantics.typesys import IteratorType, DynamicArrayType as SushiDynamicArrayType

    # Determine element type from semantic information
    if isinstance(call.receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(call.receiver.id)
        if isinstance(semantic_type, SushiDynamicArrayType):
            element_semantic_type = semantic_type.base_type
        else:
            raise_internal_error("CE0042", type=type(semantic_type).__name__)
    else:
        raise NotImplementedError("iter() on complex dynamic array expressions not fully supported yet")

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    # Allocate and initialize iterator struct
    iterator_slot = codegen.builder.alloca(iterator_struct_type)

    # Set current_index = 0
    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    # Set length = extracted length
    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(length, length_ptr)

    # Set data_ptr = extracted data pointer
    data_ptr_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr_out)

    # Load and return the iterator struct
    return codegen.builder.load(iterator_slot)


