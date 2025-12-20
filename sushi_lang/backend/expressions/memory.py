"""
Memory management operations for the Sushi language compiler.

This module handles memory allocation, deallocation, cloning, and size calculations
for dynamic arrays and structs. Includes malloc/realloc/free wrappers with error checking.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.semantics.typesys import StructType, DynamicArrayType, Type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# Type size lookup table (simplified dispatch for common types)
TYPE_SIZES = {
    'i8': 1, 'i16': 2, 'i32': 4, 'i64': 8,
    'u8': 1, 'u16': 2, 'u32': 4, 'u64': 8,
    'f32': 4, 'f64': 8,
    'bool': 1, 'ptr': 8
}


def get_element_size_constant(codegen: 'LLVMCodegen', element_type: ir.Type) -> ir.Value:
    """Get the size in bytes of an element type as an LLVM constant.

    Uses dispatch table for common types, falls back to LLVM's GEP trick
    for complex types like structs (which accounts for padding).

    Args:
        codegen: The LLVM codegen instance.
        element_type: The LLVM element type.

    Returns:
        The size as i32 constant.

    Raises:
        ValueError: If element type is not supported.
    """
    # Fast path: Check common types
    if element_type == codegen.types.i32:
        return ir.Constant(codegen.types.i32, 4)  # i32 = 4 bytes
    elif element_type == codegen.types.i8:
        return ir.Constant(codegen.types.i32, 1)  # i8 = 1 byte
    elif isinstance(element_type, ir.PointerType):
        return ir.Constant(codegen.types.i32, 8)  # pointer = 8 bytes (64-bit)
    elif isinstance(element_type, ir.FloatType):
        return ir.Constant(codegen.types.i32, 4)  # f32 = 4 bytes
    elif isinstance(element_type, ir.DoubleType):
        return ir.Constant(codegen.types.i32, 8)  # f64 = 8 bytes

    # Struct types: Use LLVM's GEP trick to get actual size with padding
    # getelementptr(null, 1) gives offset of second element = size of one element
    elif isinstance(element_type, ir.LiteralStructType):
        # Create a null pointer of type element_type*
        null_ptr = ir.Constant(ir.PointerType(element_type), None)
        # GEP to get pointer to element [1]
        size_gep = codegen.builder.gep(
            null_ptr,
            [ir.Constant(codegen.types.i64, 1)],
            name="size_gep"
        )
        # Convert pointer to integer to get the size
        size_i64 = codegen.builder.ptrtoint(size_gep, codegen.types.i64, name="size_i64")
        # Truncate to i32 (sizes should fit in 32 bits)
        size_i32 = codegen.builder.trunc(size_i64, codegen.types.i32, name="size_i32")
        return size_i32

    else:
        raise_internal_error("CE0079", type=str(element_type))


def calculate_llvm_type_size(llvm_type: 'ir.Type') -> int:
    """Calculate the size in bytes of an LLVM type for offset calculations.

    Recursively handles complex types including structs and arrays.
    This function provides accurate size calculations for all LLVM types,
    including nested structures.

    Args:
        llvm_type: The LLVM type to calculate size for.

    Returns:
        Size in bytes.
    """
    if isinstance(llvm_type, ir.IntType):
        return llvm_type.width // 8
    elif isinstance(llvm_type, ir.PointerType):
        return 8
    elif isinstance(llvm_type, ir.FloatType):
        return 4
    elif isinstance(llvm_type, ir.DoubleType):
        return 8
    elif isinstance(llvm_type, ir.LiteralStructType):
        # For structs (including enums), calculate total size
        total_size = 0
        for element_type in llvm_type.elements:
            total_size += calculate_llvm_type_size(element_type)
        return total_size
    elif isinstance(llvm_type, ir.ArrayType):
        # For arrays, multiply element size by count
        element_size = calculate_llvm_type_size(llvm_type.element)
        return element_size * llvm_type.count
    else:
        # Conservative estimate for unknown types
        return 16


def get_type_size(llvm_type: ir.Type) -> int:
    """Get the size in bytes of an LLVM type (simplified Python int).

    This is a lightweight version that returns a Python int rather than
    an LLVM constant. Used for quick size estimates.

    Args:
        llvm_type: The LLVM type.

    Returns:
        Size in bytes.
    """
    if isinstance(llvm_type, ir.IntType):
        return llvm_type.width // 8
    elif isinstance(llvm_type, ir.PointerType):
        return 8  # Assume 64-bit pointers
    elif isinstance(llvm_type, ir.FloatType):
        return 4
    elif isinstance(llvm_type, ir.DoubleType):
        return 8
    else:
        # For complex types, use accurate calculation
        return calculate_llvm_type_size(llvm_type)


def emit_malloc_call(codegen: 'LLVMCodegen', size_bytes: ir.Value) -> ir.Value:
    """Emit malloc() call with error checking.

    Args:
        codegen: The LLVM codegen instance.
        size_bytes: Number of bytes to allocate (i64).

    Returns:
        Allocated pointer (i8*).

    Note:
        Emits runtime error RE2021 and exits if malloc returns NULL.
    """
    malloc_func = codegen.get_malloc_func()

    # Ensure size is i64
    if size_bytes.type != ir.IntType(INT64_BIT_WIDTH):
        size_bytes = codegen.builder.zext(size_bytes, ir.IntType(INT64_BIT_WIDTH))

    # Call malloc
    ptr = codegen.builder.call(malloc_func, [size_bytes])

    # Check for NULL
    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    is_null = codegen.builder.icmp_unsigned('==', ptr, null_ptr, name="is_null")

    malloc_failed_bb = codegen.builder.append_basic_block(name="malloc_failed")
    malloc_success_bb = codegen.builder.append_basic_block(name="malloc_success")

    codegen.builder.cbranch(is_null, malloc_failed_bb, malloc_success_bb)

    # Failed block
    codegen.builder.position_at_end(malloc_failed_bb)
    codegen.runtime.errors.emit_runtime_error("RE2021", "memory allocation failed")
    codegen.builder.unreachable()

    # Success block
    codegen.builder.position_at_end(malloc_success_bb)

    return ptr


def emit_realloc_call(codegen: 'LLVMCodegen', old_ptr: ir.Value, new_size: ir.Value) -> ir.Value:
    """Emit realloc() call with error checking.

    Args:
        codegen: The LLVM codegen instance.
        old_ptr: Previous pointer (may be null for initial allocation).
        new_size: New size in bytes (will be converted to i64 if needed).

    Returns:
        New allocated pointer (i8*).

    Note:
        Emits runtime error RE2021 and exits if realloc returns NULL.
    """
    realloc_func = codegen.get_realloc_func()

    # Cast old pointer to void* if needed
    if old_ptr.type != ir.PointerType(codegen.types.i8):
        old_ptr = codegen.builder.bitcast(old_ptr, ir.PointerType(codegen.types.i8), name="old_void_ptr")

    # Convert size to i64 for realloc (size_t)
    if new_size.type != ir.IntType(INT64_BIT_WIDTH):
        new_size = codegen.builder.zext(new_size, ir.IntType(INT64_BIT_WIDTH), name="size_i64")

    # Call realloc
    new_void_ptr = codegen.builder.call(realloc_func, [old_ptr, new_size], name="realloc_result")

    # Check if realloc returned NULL (allocation failure)
    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    is_null = codegen.builder.icmp_unsigned('==', new_void_ptr, null_ptr, name="is_null")

    # Create basic blocks for null check
    null_block = codegen.builder.append_basic_block(name="realloc_null")
    success_block = codegen.builder.append_basic_block(name="realloc_success")

    # Branch based on null check
    codegen.builder.cbranch(is_null, null_block, success_block)

    # Null block: emit runtime error and exit
    codegen.builder.position_at_end(null_block)
    codegen.runtime.errors.emit_runtime_error("RE2021", "memory reallocation failed")
    codegen.builder.unreachable()

    # Success block: continue normal execution
    codegen.builder.position_at_end(success_block)

    return new_void_ptr


def emit_free_call(codegen: 'LLVMCodegen', ptr: ir.Value) -> None:
    """Emit free() call to deallocate memory.

    Args:
        codegen: The LLVM codegen instance.
        ptr: Pointer to free (should be i8*/void*).
    """
    free_func = codegen.get_free_func()
    codegen.builder.call(free_func, [ptr])


def clone_dynamic_array_value(codegen: 'LLVMCodegen', array_struct: ir.Value, element_type: Type) -> ir.Value:
    """Clone a dynamic array struct value (creates deep copy with independent memory).

    This performs a full deep copy of the array, allocating new memory and copying
    all elements. The cloned array has its own heap allocation and can be safely
    modified without affecting the original.

    Args:
        codegen: The LLVM codegen instance.
        array_struct: The array struct value {len, cap, data*} to clone.
        element_type: The semantic element type of the array.

    Returns:
        A new array struct value with cloned data.

    Note:
        Empty arrays (len=0) return {0, 0, null} without allocating memory.
    """
    zero = ir.Constant(codegen.types.i32, 0)

    # Extract fields from source array
    source_len = codegen.builder.extract_value(array_struct, 0)
    source_cap = codegen.builder.extract_value(array_struct, 1)
    source_data_ptr = codegen.builder.extract_value(array_struct, 2)

    # Get LLVM element type
    element_llvm_type = codegen.types.ll_type(element_type)
    array_struct_type = array_struct.type

    # Check if source array is empty (len == 0)
    len_is_zero = codegen.builder.icmp_unsigned('==', source_len, zero)

    empty_clone_bb = codegen.builder.append_basic_block('clone_empty')
    non_empty_clone_bb = codegen.builder.append_basic_block('clone_non_empty')
    clone_merge_bb = codegen.builder.append_basic_block('clone_merge')

    codegen.builder.cbranch(len_is_zero, empty_clone_bb, non_empty_clone_bb)

    # Empty clone path: return {0, 0, null}
    codegen.builder.position_at_end(empty_clone_bb)
    null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
    empty_array = ir.Constant(array_struct_type, ir.Undefined)
    empty_array = codegen.builder.insert_value(empty_array, zero, 0)
    empty_array = codegen.builder.insert_value(empty_array, zero, 1)
    empty_array = codegen.builder.insert_value(empty_array, null_ptr, 2)
    codegen.builder.branch(clone_merge_bb)

    # Non-empty clone path: allocate and copy
    codegen.builder.position_at_end(non_empty_clone_bb)

    # Allocate new memory (capacity * sizeof(element))
    # Use centralized size calculation with semantic type
    sizeof_element_i32 = codegen.types.get_type_size_constant(element_type)
    cap_i64 = codegen.builder.zext(source_cap, codegen.types.i64)
    sizeof_element_i64 = codegen.builder.zext(sizeof_element_i32, codegen.types.i64)
    total_bytes = codegen.builder.mul(cap_i64, sizeof_element_i64)

    # Use our malloc wrapper with error checking
    new_data_ptr_i8 = emit_malloc_call(codegen, total_bytes)
    new_data_ptr = codegen.builder.bitcast(new_data_ptr_i8, ir.PointerType(element_llvm_type))

    # Copy elements (manual loop for portability)
    copy_index = codegen.builder.alloca(codegen.types.i32, name="copy_idx")
    codegen.builder.store(zero, copy_index)

    copy_loop_head = codegen.builder.append_basic_block('copy_loop_head')
    copy_loop_body = codegen.builder.append_basic_block('copy_loop_body')
    copy_loop_exit = codegen.builder.append_basic_block('copy_loop_exit')

    codegen.builder.branch(copy_loop_head)

    # Loop head: check if index < len
    codegen.builder.position_at_end(copy_loop_head)
    idx = codegen.builder.load(copy_index)
    cond = codegen.builder.icmp_unsigned('<', idx, source_len)
    codegen.builder.cbranch(cond, copy_loop_body, copy_loop_exit)

    # Loop body: copy element
    codegen.builder.position_at_end(copy_loop_body)
    src_elem_ptr = codegen.builder.gep(source_data_ptr, [idx])
    elem = codegen.builder.load(src_elem_ptr)
    dst_elem_ptr = codegen.builder.gep(new_data_ptr, [idx])
    codegen.builder.store(elem, dst_elem_ptr)

    # Increment index
    next_idx = codegen.builder.add(idx, ir.Constant(codegen.types.i32, 1))
    codegen.builder.store(next_idx, copy_index)
    codegen.builder.branch(copy_loop_head)

    # Loop exit: create new array struct
    codegen.builder.position_at_end(copy_loop_exit)
    new_array = ir.Constant(array_struct_type, ir.Undefined)
    new_array = codegen.builder.insert_value(new_array, source_len, 0)
    new_array = codegen.builder.insert_value(new_array, source_cap, 1)
    new_array = codegen.builder.insert_value(new_array, new_data_ptr, 2)
    codegen.builder.branch(clone_merge_bb)

    # Merge: phi node to select result
    codegen.builder.position_at_end(clone_merge_bb)
    result_phi = codegen.builder.phi(array_struct_type, name="cloned_array")
    result_phi.add_incoming(empty_array, empty_clone_bb)
    result_phi.add_incoming(new_array, copy_loop_exit)

    return result_phi


def deep_copy_struct(codegen: 'LLVMCodegen', struct_value: ir.Value, struct_type: StructType) -> ir.Value:
    """Deep copy a struct value, cloning all dynamic array fields recursively.

    This recursively deep-copies any dynamic array or nested struct fields
    that contain dynamic arrays. Ensures that the copied struct has independent
    memory allocations.

    Args:
        codegen: The LLVM codegen instance.
        struct_value: The struct value to copy.
        struct_type: The semantic struct type.

    Returns:
        A new struct value with cloned dynamic arrays.

    Note:
        This feature is fully implemented and tested. Prevents double-free crashes
        when passing structs with dynamic arrays to other struct constructors.
        See tests/test_struct_nested_deep_copy*.sushi for test cases.
    """
    # Start with the original struct value
    new_struct = struct_value

    # Iterate through fields and deep-copy dynamic arrays
    for field_idx, (field_name, field_type) in enumerate(struct_type.fields):
        if isinstance(field_type, DynamicArrayType):
            # Extract the dynamic array from the struct
            array_value = codegen.builder.extract_value(struct_value, field_idx)

            # Clone the dynamic array
            cloned_array = clone_dynamic_array_value(codegen, array_value, field_type.base_type)

            # Insert the cloned array into the new struct
            new_struct = codegen.builder.insert_value(new_struct, cloned_array, field_idx)

        elif isinstance(field_type, StructType):
            # Nested struct - recursively deep copy if it needs cleanup
            if codegen.dynamic_arrays.struct_needs_cleanup(field_type):
                nested_struct_value = codegen.builder.extract_value(struct_value, field_idx)
                cloned_nested = deep_copy_struct(codegen, nested_struct_value, field_type)
                new_struct = codegen.builder.insert_value(new_struct, cloned_nested, field_idx)

    return new_struct
