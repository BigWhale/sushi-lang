"""
Simple List<T> methods: new(), with_capacity(), len(), capacity(), is_empty().

These are straightforward methods that don't involve complex logic.
"""

from typing import Any
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_llvm_type, get_list_len_ptr, get_list_capacity_ptr, extract_element_type
from sushi_lang.backend.llvm_constants import LIST_DATA_INDICES


def emit_list_new(codegen: Any, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for List<T>.new() - create empty list.

    Creates a List<T> with len=0, capacity=0, data=null.
    Lazy allocation - no memory allocated until first push.

    Args:
        codegen: LLVM codegen instance.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        A List<T> struct value with zero length, zero capacity, null data.
    """
    # Get LLVM type for List<T> from the type system cache
    # This ensures we use the same type instance that variables use
    list_llvm_type = codegen.types.ll_type(list_type)

    # Create struct with zero values
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    null_ptr = ir.Constant(list_llvm_type.elements[2], None)  # null pointer

    # Build struct: {i32 len=0, i32 capacity=0, T* data=null}
    list_value = ir.Constant(list_llvm_type, [zero_i32, zero_i32, null_ptr])

    return list_value


def emit_list_with_capacity(codegen: Any, expr: Any, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for List<T>.with_capacity(n) - pre-allocate list.

    Creates a List<T> with len=0, capacity=n, data=malloc(n * sizeof(T)).

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        A List<T> struct value with zero length, specified capacity, allocated data.
    """
    from sushi_lang.backend.expressions import memory

    # Extract element type from List<T>
    element_type = extract_element_type(list_type, codegen)

    # Get LLVM type for List<T>
    list_llvm_type = get_list_llvm_type(codegen, element_type)
    element_llvm_type = list_llvm_type.elements[2].pointee

    # Evaluate capacity argument
    capacity_arg = codegen.expressions.emit_expr(expr.args[0])

    # Ensure capacity_arg is i32 (truncate if i64)
    if capacity_arg.type != codegen.types.i32:
        if capacity_arg.type.width > 32:
            capacity_arg = codegen.builder.trunc(capacity_arg, codegen.types.i32, name="capacity_i32")
        elif capacity_arg.type.width < 32:
            capacity_arg = codegen.builder.sext(capacity_arg, codegen.types.i32, name="capacity_i32")

    # Calculate allocation size: capacity * sizeof(T)
    element_size = memory.get_element_size_constant(codegen, element_llvm_type)
    # Extend both to i64 for size calculation (malloc expects i64)
    capacity_i64 = codegen.builder.zext(capacity_arg, codegen.types.i64, name="capacity_i64")
    element_size_i64 = codegen.builder.zext(element_size, codegen.types.i64, name="element_size_i64")
    alloc_size = codegen.builder.mul(capacity_i64, element_size_i64, name="alloc_size")

    # Allocate memory
    malloc_func = codegen.get_malloc_func()
    data_void_ptr = codegen.builder.call(malloc_func, [alloc_size], name="list_data_void")

    # Cast to T*
    data_ptr = codegen.builder.bitcast(
        data_void_ptr,
        ir.PointerType(element_llvm_type),
        name="list_data"
    )

    # Create struct: {i32 len=0, i32 capacity=n, T* data=ptr}
    zero_i32 = ir.Constant(codegen.types.i32, 0)

    # Allocate stack space for the struct
    list_alloca = codegen.builder.alloca(list_llvm_type, name="list")

    # Store fields
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    codegen.builder.store(zero_i32, len_ptr)
    codegen.builder.store(capacity_arg, capacity_ptr)
    codegen.builder.store(data_ptr, data_ptr_ptr)

    # Load and return the complete struct
    return codegen.builder.load(list_alloca, name="list_value")


def emit_list_len(codegen: Any, list_ptr: ir.Value) -> ir.Value:
    """Emit LLVM IR for list.len() - get current length.

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.

    Returns:
        The length as i32.
    """
    # Get len field
    len_ptr = get_list_len_ptr(codegen.builder, list_ptr)
    return codegen.builder.load(len_ptr, name="list_len")


def emit_list_capacity(codegen: Any, list_ptr: ir.Value) -> ir.Value:
    """Emit LLVM IR for list.capacity() - get allocated capacity.

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.

    Returns:
        The capacity as i32.
    """
    # Get capacity field
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_ptr)
    return codegen.builder.load(capacity_ptr, name="list_capacity")


def emit_list_is_empty(codegen: Any, list_ptr: ir.Value) -> ir.Value:
    """Emit LLVM IR for list.is_empty() - check if length is zero.

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.

    Returns:
        True (i8 1) if length is 0, False (i8 0) otherwise.
    """
    len_value = emit_list_len(codegen, list_ptr)

    # Compare len == 0
    zero = ir.Constant(codegen.types.i32, 0)
    is_zero = codegen.builder.icmp_signed('==', len_value, zero, name="is_empty")

    # Convert i1 to i8
    return codegen.builder.zext(is_zero, codegen.types.i8)
