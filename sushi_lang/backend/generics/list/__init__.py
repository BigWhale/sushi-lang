"""
Built-in extension methods for List<T> generic struct.

INLINE EMISSION ONLY. List<T> methods work on-demand for all types.

Implemented methods:
- new() -> List<T>: Create empty list (0 capacity, lazy allocation)
- with_capacity(i32) -> List<T>: Pre-allocate list with specified capacity
- len() -> i32: Get current number of elements
- capacity() -> i32: Get allocated capacity
- is_empty() -> bool: Check if length is zero
- push(T) -> ~: Append element (auto-grows 2x when needed)
- pop() -> Maybe<T>: Remove and return last element
- get(i32) -> Maybe<T>: Safe element access by index
- clear() -> ~: Remove all elements (keep capacity, destroy elements)
- reserve(i32) -> ~: Ensure capacity for additional elements
- shrink_to_fit() -> ~: Reduce capacity to exact length
- destroy() -> ~: Recursively destroy elements, free memory (unusable after)
- free() -> ~: Recursively destroy elements, reset to empty (still usable)
- debug() -> ~: Print internal state for debugging
- iter() -> Iterator<T>: Create iterator for foreach loops

The List<T> type is a generic struct with automatic growth:
- struct List<T>:
  - i32 len       # Current number of elements
  - i32 capacity  # Allocated capacity
  - T* data       # Pointer to heap-allocated array
- Uses 2x exponential growth on push (amortized O(1))
- Initial capacity: 0 (lazy allocation on first push)
- Methods returning Maybe<T>: pop(), get()
- RAII cleanup on scope exit (automatic destroy())

This module provides list methods that work with List<T> after monomorphization.
"""

from typing import Any, Optional
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type
import llvmlite.ir as ir

from sushi_lang.internals.errors import raise_internal_error

# Public API - LLVM emission
from .methods_simple import (
    emit_list_new,
    emit_list_with_capacity,
    emit_list_len,
    emit_list_capacity,
    emit_list_is_empty
)
from .methods_modify import (
    emit_list_push,
    emit_list_pop,
    emit_list_get,
    emit_list_clear,
    emit_list_insert,
    emit_list_remove
)
from .methods_capacity import (
    emit_list_reserve,
    emit_list_shrink_to_fit
)
from .methods_destroy import (
    emit_list_destroy,
    emit_list_free
)
from .methods_debug import (
    emit_list_debug
)
from .methods_iter import (
    emit_list_iter
)


def emit_list_method(
    codegen: Any,
    expr: MethodCall,
    receiver_value: ir.Value,
    receiver_type: StructType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM IR for List<T> method calls.

    List<T> is a CORE language feature that uses inline emission.
    This function dispatches to specialized emitters based on method name.

    Args:
        codegen: The LLVM code generator.
        expr: The method call expression.
        receiver_value: The LLVM value of the List (None for new()/with_capacity()).
        receiver_type: The List<T> struct type.
        to_i1: Whether to convert result to i1.

    Returns:
        The result of the List method call.

    Raises:
        ValueError: If method name is not recognized.
    """
    method = expr.method

    # Dispatch to method-specific emitters
    if method == "new":
        result = emit_list_new(codegen, receiver_type)
    elif method == "with_capacity":
        result = emit_list_with_capacity(codegen, expr, receiver_type)
    elif method == "len":
        result = emit_list_len(codegen, receiver_value)
    elif method == "capacity":
        result = emit_list_capacity(codegen, receiver_value)
    elif method == "is_empty":
        result = emit_list_is_empty(codegen, receiver_value)
    elif method == "push":
        from sushi_lang.backend.expressions.memory import move_owning_arg_into_container
        result = emit_list_push(codegen, expr, receiver_value, receiver_type)
        move_owning_arg_into_container(codegen, expr.args[0])
    elif method == "pop":
        result = emit_list_pop(codegen, receiver_value, receiver_type)
    elif method == "get":
        result = emit_list_get(codegen, expr, receiver_value, receiver_type)
    elif method == "clear":
        result = emit_list_clear(codegen, receiver_value, receiver_type)
    elif method == "insert":
        from sushi_lang.backend.expressions.memory import move_owning_arg_into_container
        result = emit_list_insert(codegen, expr, receiver_value, receiver_type)
        if len(expr.args) >= 2:
            move_owning_arg_into_container(codegen, expr.args[1])
    elif method == "remove":
        result = emit_list_remove(codegen, expr, receiver_value, receiver_type)
    elif method == "reserve":
        result = emit_list_reserve(codegen, expr, receiver_value, receiver_type)
    elif method == "shrink_to_fit":
        result = emit_list_shrink_to_fit(codegen, receiver_value, receiver_type)
    elif method == "destroy":
        result = emit_list_destroy(codegen, receiver_value, receiver_type)
    elif method == "free":
        result = emit_list_free(codegen, receiver_value, receiver_type)
    elif method == "debug":
        result = emit_list_debug(codegen, receiver_value, receiver_type)
    elif method == "iter":
        result = emit_list_iter(codegen, expr, receiver_value, receiver_type)
    else:
        raise_internal_error("CE0083", method=method)

    # Convert to i1 if needed
    if to_i1 and method == "is_empty":
        result = codegen.utils.as_i1(result)

    return result


