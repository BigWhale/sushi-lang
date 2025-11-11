"""
List<T> capacity management methods: reserve(), shrink_to_fit().

These methods manage memory allocation without changing the logical size.
"""

from typing import Any
from semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_len_ptr, get_list_capacity_ptr, get_list_element_type
from backend.llvm_constants import LIST_DATA_INDICES


def emit_list_reserve(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.reserve(additional) - ensure capacity for more elements.

    Ensures list has capacity for at least (len + additional) elements.
    Only grows, never shrinks.

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        Updated List<T> struct value.
    """
    from backend.expressions import memory

    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get pointers
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Evaluate additional argument
    additional = codegen.expressions.emit_expr(expr.args[0])

    # Ensure additional is i32 (truncate if i64)
    if additional.type != codegen.types.i32:
        if additional.type.width > 32:
            additional = codegen.builder.trunc(additional, codegen.types.i32, name="additional_i32")
        elif additional.type.width < 32:
            additional = codegen.builder.sext(additional, codegen.types.i32, name="additional_i32")

    # Calculate needed capacity: len + additional
    needed_cap = codegen.builder.add(current_len, additional, name="needed_cap")

    # Check if growth needed
    need_growth = codegen.builder.icmp_unsigned(">", needed_cap, current_cap)

    before_if = codegen.builder.block

    with codegen.builder.if_then(need_growth):
        # Reallocate to needed_cap
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        new_total_size = codegen.builder.mul(needed_cap, element_size, name="new_total_size")

        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)
        typed_new_data_ptr = codegen.builder.bitcast(
            new_data_ptr,
            ir.PointerType(element_llvm_type),
            name="typed_new_data_ptr"
        )

        # Update capacity and data pointer
        codegen.builder.store(needed_cap, capacity_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        after_if = codegen.builder.block

    # Return updated list
    return codegen.builder.load(list_alloca, name="updated_list")


def emit_list_shrink_to_fit(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.shrink_to_fit() - reduce capacity to exact length.

    Reallocates to capacity == len to save memory.
    If len == 0, frees data and sets capacity to 0.

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        Updated List<T> struct value.
    """
    from backend.expressions import memory

    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get pointers
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Check if len != cap (needs shrinking)
    need_shrink = codegen.builder.icmp_unsigned("!=", current_len, current_cap)

    with codegen.builder.if_then(need_shrink):
        # Check if len == 0 (free completely)
        zero = ir.Constant(codegen.types.i32, 0)
        is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

        with codegen.builder.if_then(is_empty):
            # Free data
            free_func = codegen.get_free_func()
            data_void_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(codegen.types.i8))
            codegen.builder.call(free_func, [data_void_ptr])

            # Set data to null, capacity to 0
            null_ptr = ir.Constant(data_ptr.type, None)
            codegen.builder.store(null_ptr, data_ptr_ptr)
            codegen.builder.store(zero, capacity_ptr)

        # Not empty: realloc to exact size
        empty_block_end = codegen.builder.block
        not_empty_block = codegen.func.append_basic_block("shrink_not_empty")
        after_empty_check = codegen.func.append_basic_block("after_empty_check")

        codegen.builder.cbranch(is_empty, after_empty_check, not_empty_block)

        codegen.builder.position_at_end(not_empty_block)
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        new_total_size = codegen.builder.mul(current_len, element_size, name="new_total_size")

        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)
        typed_new_data_ptr = codegen.builder.bitcast(
            new_data_ptr,
            ir.PointerType(element_llvm_type),
            name="typed_new_data_ptr"
        )

        codegen.builder.store(current_len, capacity_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        codegen.builder.branch(after_empty_check)

        codegen.builder.position_at_end(after_empty_check)

    # Return updated list
    return codegen.builder.load(list_alloca, name="updated_list")
