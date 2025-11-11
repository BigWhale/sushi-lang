"""
Array cloning and type conversion operations.

This module handles:
- Array deep cloning (creating independent copies)
- Byte array to string conversion (UTF-8)
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.llvm_constants import ZERO_I8, make_i32_const
from semantics.ast import MethodCall
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen

def emit_byte_array_to_string(codegen: "LLVMCodegen", call: MethodCall, receiver_value: ir.Value,
                               receiver_type: ir.LiteralStructType, _to_i1: bool) -> ir.Value:
    """Emit LLVM IR for u8[] to_string() method (convert byte array to UTF-8 string).

    This is a zero-cost conversion that assumes the byte array contains valid UTF-8.
    No validation is performed for performance reasons.

    IMPORTANT: Invalid UTF-8 sequences result in undefined behavior. Use this method
    only when you're certain the bytes are valid UTF-8 (e.g., from trusted sources,
    file I/O with known encoding, or network protocols with UTF-8 guarantees).

    Future: A stdlib function bytes_to_string_checked() will provide validation
    and return Result<string> for safety-critical use cases.

    Args:
        receiver_value: Pointer to dynamic array struct {i32 len, i32 cap, u8* data}

    Returns:
        Fat pointer struct {i8* data, i32 size} containing the string data
    """
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="to_string", expected=0, got=len(call.args))

    zero = make_i32_const(0)

    # Extract length from array struct (field 0)
    len_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(0)])
    byte_count = codegen.builder.load(len_ptr)

    # Extract data pointer from array struct (field 2)
    data_ptr_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(2)])
    data_ptr = codegen.builder.load(data_ptr_ptr)

    # Allocate memory for string (byte_count + 1 for null terminator)
    malloc_func = codegen.get_malloc_func()
    string_size = codegen.builder.add(byte_count, ir.Constant(codegen.types.i32, 1))
    string_size_i64 = codegen.builder.zext(string_size, ir.IntType(INT64_BIT_WIDTH))
    string_ptr = codegen.builder.call(malloc_func, [string_size_i64], name="to_string_ptr")

    # Check if malloc failed
    null_ptr = ir.Constant(ir.PointerType(ir.IntType(INT8_BIT_WIDTH)), None)
    is_null = codegen.builder.icmp_unsigned('==', string_ptr, null_ptr)

    malloc_fail_bb = codegen.builder.append_basic_block('to_string_malloc_fail')
    malloc_success_bb = codegen.builder.append_basic_block('to_string_malloc_success')

    codegen.builder.cbranch(is_null, malloc_fail_bb, malloc_success_bb)

    # Malloc failed - emit runtime error
    codegen.builder.position_at_end(malloc_fail_bb)
    codegen.runtime.errors.emit_runtime_error("RE2021", "memory allocation failed")
    codegen.builder.unreachable()

    # Malloc succeeded - copy bytes from array to string
    codegen.builder.position_at_end(malloc_success_bb)

    # PERFORMANCE: No UTF-8 validation for fast conversion
    # This method assumes byte arrays contain valid UTF-8 for zero-cost conversion.
    # Invalid UTF-8 sequences result in undefined behavior (similar to unsafe casts).
    #
    # FUTURE: A stdlib function bytes_to_string_checked() can provide validation
    # and return Result<string> for safety-critical code paths.
    #
    # Design rationale: Most real-world byte arrays (file I/O, network protocols)
    # contain valid UTF-8, so validation overhead is unnecessary in the common case.

    # Copy bytes using shared loop helper
    from backend.statements.utils import emit_copy_loop
    emit_copy_loop(
        codegen=codegen,
        count=byte_count,
        src_ptr=data_ptr,
        dst_ptr=string_ptr,
        element_type=codegen.types.i8,
        name_prefix="to_string"
    )

    # Loop done: add null terminator
    null_term_ptr = codegen.builder.gep(string_ptr, [byte_count])
    codegen.builder.store(ZERO_I8, null_term_ptr)

    # Build fat pointer struct: {i8* data, i32 size}
    string_struct_type = codegen.types.string_struct
    undef_struct = ir.Constant(string_struct_type, ir.Undefined)
    struct_with_data = codegen.builder.insert_value(undef_struct, string_ptr, 0)
    struct_complete = codegen.builder.insert_value(struct_with_data, byte_count, 1)

    return struct_complete


def emit_dynamic_array_clone(codegen: "LLVMCodegen", call: MethodCall, receiver_value: ir.Value,
                              receiver_type: ir.LiteralStructType, _to_i1: bool) -> ir.Value:
    """Emit LLVM IR for dynamic array clone() method (deep copy).

    Creates an independent copy of the dynamic array with its own heap memory.
    This enables explicit cloning when needed (e.g., arr2 := arr1.clone()).

    Implementation:
    1. Extract len, cap, and data pointer from source array
    2. Allocate new memory with same capacity
    3. Copy all elements from source to new memory
    4. Return new array struct with independent memory

    Args:
        receiver_value: Pointer to dynamic array struct {i32 len, i32 cap, T* data}

    Returns:
        New dynamic array struct (by value) with independent memory
    """
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="clone", expected=0, got=len(call.args))

    zero = make_i32_const(0)

    # Extract fields from source array struct
    len_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(0)])
    source_len = codegen.builder.load(len_ptr)

    cap_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(1)])
    source_cap = codegen.builder.load(cap_ptr)

    data_ptr_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(2)])
    source_data_ptr = codegen.builder.load(data_ptr_ptr)

    # Get element type from receiver_type (struct {i32, i32, T*})
    element_type_llvm = receiver_type.elements[2].pointee

    # Check if source array is empty (len == 0)
    len_is_zero = codegen.builder.icmp_unsigned('==', source_len, zero)

    empty_clone_bb = codegen.builder.append_basic_block('clone_empty')
    non_empty_clone_bb = codegen.builder.append_basic_block('clone_non_empty')
    clone_merge_bb = codegen.builder.append_basic_block('clone_merge')

    codegen.builder.cbranch(len_is_zero, empty_clone_bb, non_empty_clone_bb)

    # Empty clone path: return {0, 0, null}
    codegen.builder.position_at_end(empty_clone_bb)
    null_ptr = ir.Constant(ir.PointerType(element_type_llvm), None)
    empty_array = ir.Constant(receiver_type, ir.Undefined)
    empty_array = codegen.builder.insert_value(empty_array, zero, 0)
    empty_array = codegen.builder.insert_value(empty_array, zero, 1)
    empty_array = codegen.builder.insert_value(empty_array, null_ptr, 2)
    codegen.builder.branch(clone_merge_bb)

    # Non-empty clone path: allocate and copy
    codegen.builder.position_at_end(non_empty_clone_bb)

    # Calculate size in bytes: capacity * element_size
    from backend.expressions import memory
    element_size = memory.get_element_size_constant(codegen, element_type_llvm)
    total_bytes = codegen.builder.mul(source_cap, element_size, name="clone_total_bytes")

    # Allocate new memory
    malloc_func = codegen.get_malloc_func()
    total_bytes_i64 = codegen.builder.zext(total_bytes, ir.IntType(INT64_BIT_WIDTH))
    new_data_ptr = codegen.builder.call(malloc_func, [total_bytes_i64], name="clone_data_ptr")

    # Check if malloc failed
    null_i8_ptr = ir.Constant(ir.PointerType(ir.IntType(INT8_BIT_WIDTH)), None)
    malloc_failed = codegen.builder.icmp_unsigned('==', new_data_ptr, null_i8_ptr)

    malloc_fail_bb = codegen.builder.append_basic_block('clone_malloc_fail')
    malloc_success_bb = codegen.builder.append_basic_block('clone_malloc_success')

    codegen.builder.cbranch(malloc_failed, malloc_fail_bb, malloc_success_bb)

    # Malloc failed - emit runtime error
    codegen.builder.position_at_end(malloc_fail_bb)
    codegen.runtime.errors.emit_runtime_error("RE2021", "memory allocation failed in array clone")
    codegen.builder.unreachable()

    # Malloc succeeded - copy elements
    codegen.builder.position_at_end(malloc_success_bb)

    # Cast void* to element_type*
    typed_new_data_ptr = codegen.builder.bitcast(new_data_ptr, ir.PointerType(element_type_llvm))

    # Copy elements using shared loop helper
    from backend.statements.utils import emit_copy_loop
    emit_copy_loop(
        codegen=codegen,
        count=source_len,
        src_ptr=source_data_ptr,
        dst_ptr=typed_new_data_ptr,
        element_type=element_type_llvm,
        name_prefix="clone_copy"
    )

    # Loop done: create new array struct
    # Note: Builder is now positioned at clone_copy_loop_done block after emit_copy_loop
    new_array = ir.Constant(receiver_type, ir.Undefined)
    new_array = codegen.builder.insert_value(new_array, source_len, 0)
    new_array = codegen.builder.insert_value(new_array, source_cap, 1)
    new_array = codegen.builder.insert_value(new_array, typed_new_data_ptr, 2)

    # Get the current block before branching (this is where the PHI comes from)
    clone_copy_done_bb = codegen.builder.block
    codegen.builder.branch(clone_merge_bb)

    # Merge block: phi node to select result
    codegen.builder.position_at_end(clone_merge_bb)
    result_phi = codegen.builder.phi(receiver_type, name="clone_result")
    result_phi.add_incoming(empty_array, empty_clone_bb)
    result_phi.add_incoming(new_array, clone_copy_done_bb)

    # Allocate stack storage for the cloned array struct and return pointer
    # (This matches the pattern used by to_bytes() for returning dynamic arrays)
    clone_slot = codegen.builder.alloca(receiver_type, name="clone_slot")
    codegen.builder.store(result_phi, clone_slot)

    return clone_slot

