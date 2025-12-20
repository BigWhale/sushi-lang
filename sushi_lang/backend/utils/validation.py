"""Validation utilities for common checks in code generation.

This module provides reusable validation functions to reduce repetitive
error checking patterns throughout the backend. All validation functions
raise InternalError on failure, making them suitable for use in assertions
and precondition checks.

Common Usage:
    builder = require_builder(codegen)  # Validates and returns builder
    func = require_function(codegen)    # Validates and returns function
    items = require_non_empty(some_list, "CE0020")  # Validates list
"""

from typing import TYPE_CHECKING, TypeVar

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.interfaces import CodegenProtocol

T = TypeVar('T')


def require_builder(codegen: 'CodegenProtocol') -> ir.IRBuilder:
    """Validate builder is initialized or raise CE0009.

    This is the most common validation in the backend, appearing in nearly
    every expression and statement emission function. The builder must be
    initialized before any LLVM IR instructions can be emitted.

    Args:
        codegen: Code generator instance to check.

    Returns:
        The initialized builder, ready for IR emission.

    Raises:
        InternalError CE0009: If builder is None (not initialized).

    Example:
        >>> builder = require_builder(codegen)
        >>> result = builder.add(lhs, rhs)
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")
    return codegen.builder


def require_function(codegen: 'CodegenProtocol') -> ir.Function:
    """Validate current function is set or raise CE0010.

    Some operations require access to the current function being generated,
    such as creating basic blocks or accessing function arguments. This
    validator ensures the function context is available.

    Args:
        codegen: Code generator instance to check.

    Returns:
        The current LLVM function being generated.

    Raises:
        InternalError CE0010: If func is None (no active function).

    Example:
        >>> func = require_function(codegen)
        >>> entry_block = func.append_basic_block('entry')
    """
    if codegen.func is None:
        raise_internal_error("CE0010")
    return codegen.func


def require_non_empty(items: list[T], error_code: str) -> list[T]:
    """Validate list is non-empty or raise specified error.

    Used in contexts where an empty list would be a logical error,
    such as function arguments, match cases, or enum variants.

    Args:
        items: List to validate.
        error_code: Error code to raise if list is empty (e.g., "CE0020").

    Returns:
        The same list if non-empty.

    Raises:
        InternalError: With the specified error code if list is empty.

    Example:
        >>> args = require_non_empty(call_args, "CE0015")
        >>> first_arg = args[0]
    """
    if not items:
        raise_internal_error(error_code)
    return items


def require_both_initialized(codegen: 'CodegenProtocol') -> tuple[ir.IRBuilder, ir.Function]:
    """Validate both builder and function are initialized.

    Convenience function for code paths that require both the builder
    and the current function context. Reduces two validation calls to one.

    Args:
        codegen: Code generator instance to check.

    Returns:
        Tuple of (builder, function) if both are initialized.

    Raises:
        InternalError CE0009: If builder is None.
        InternalError CE0010: If func is None.

    Example:
        >>> builder, func = require_both_initialized(codegen)
        >>> block = func.append_basic_block('then')
        >>> builder.position_at_start(block)
    """
    builder = require_builder(codegen)
    func = require_function(codegen)
    return builder, func
