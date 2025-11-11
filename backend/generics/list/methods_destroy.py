"""
List<T> destruction methods: destroy(), free().

These methods handle memory cleanup and destruction.
"""

from typing import Any
from semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_len_ptr, get_list_capacity_ptr, extract_element_type
from backend.llvm_constants import LIST_DATA_INDICES


def emit_list_destroy(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.destroy() - destroy all elements and free memory.

    Recursively destroys all elements, frees data pointer, sets data to null.
    List is unusable after this call (like HashMap.destroy()).

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        Updated List<T> struct value (with null data pointer).
    """
    element_type = extract_element_type(list_type, codegen)

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
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Destroy all elements
    from .methods_modify import _emit_destroy_elements_loop
    _emit_destroy_elements_loop(codegen, data_ptr, current_len, element_type)

    # Free data pointer
    free_func = codegen.get_free_func()
    data_void_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(codegen.types.i8))
    codegen.builder.call(free_func, [data_void_ptr])

    # Set to unusable state: data=null, len=0, capacity=0
    zero = ir.Constant(codegen.types.i32, 0)
    null_ptr = ir.Constant(data_ptr.type, None)

    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(zero, capacity_ptr)
    codegen.builder.store(null_ptr, data_ptr_ptr)

    # Return updated list
    return codegen.builder.load(list_alloca, name="destroyed_list")


def emit_list_free(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.free() - destroy elements and reset to empty.

    Recursively destroys all elements, frees data pointer, resets to empty state.
    List is still usable after this call (like HashMap.free()).

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        Empty List<T> struct value (ready for reuse).
    """
    element_type = extract_element_type(list_type, codegen)

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
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Destroy all elements
    from .methods_modify import _emit_destroy_elements_loop
    _emit_destroy_elements_loop(codegen, data_ptr, current_len, element_type)

    # Free data pointer
    free_func = codegen.get_free_func()
    data_void_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(codegen.types.i8))
    codegen.builder.call(free_func, [data_void_ptr])

    # Reset to empty but usable state: data=null, len=0, capacity=0
    zero = ir.Constant(codegen.types.i32, 0)
    null_ptr = ir.Constant(data_ptr.type, None)

    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(zero, capacity_ptr)
    codegen.builder.store(null_ptr, data_ptr_ptr)

    # Return reset list (same as .new())
    return codegen.builder.load(list_alloca, name="freed_list")
