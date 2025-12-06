"""Unified dispatcher for generic type method calls via registry.

This module provides registry-based dispatch for generic type methods,
enabling stdlib and user libraries to define generic types through
the GenericTypeProvider interface.

For Phase 1, this dispatcher works alongside the existing hardcoded
dispatchers in backend/expressions/calls/generics.py. Once all types
are migrated, the hardcoded dispatchers can be removed.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Union

from llvmlite import ir
from semantics.ast import Name, MethodCall, DotCall
from semantics.typesys import StructType, EnumType
from semantics.generics.providers.registry import GenericTypeRegistry

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def try_emit_via_registry(
    codegen: 'LLVMCodegen',
    expr: Union[MethodCall, DotCall],
    to_i1: bool = False
) -> Optional[ir.Value]:
    """Try to emit a generic type method call via the provider registry.

    This function checks if the method call target is a registered generic
    type and delegates to the provider's emit_method if so.

    Args:
        codegen: The LLVM code generator
        expr: The method call expression
        to_i1: Whether to convert boolean result to i1

    Returns:
        The emitted LLVM value if handled by a provider, None otherwise
    """
    # Extract the generic type name from the expression
    type_name = _get_generic_type_name(codegen, expr)
    if type_name is None:
        return None

    # Look up the provider in the registry
    provider = GenericTypeRegistry.get(type_name)
    if provider is None:
        return None

    # Check if the provider handles this method
    if not provider.is_valid_method(expr.method):
        return None

    # Get receiver value and type for the method call
    receiver_value, receiver_type = _prepare_receiver(codegen, expr, type_name, provider)

    # Delegate to the provider
    return provider.emit_method(
        codegen,
        expr,
        receiver_value,
        receiver_type,
        to_i1
    )


def _get_generic_type_name(
    codegen: 'LLVMCodegen',
    expr: Union[MethodCall, DotCall]
) -> Optional[str]:
    """Extract the generic type name from a method call expression.

    Handles two cases:
    1. Static calls: HashMap.new() -> receiver is Name("HashMap")
    2. Instance calls: map.get(key) -> need to check receiver type

    Args:
        codegen: The LLVM code generator
        expr: The method call expression

    Returns:
        The generic type name if found, None otherwise
    """
    receiver = expr.receiver

    # Case 1: Static method call (Type.method())
    if isinstance(receiver, Name):
        name = receiver.id
        # Check if this is a registered generic type
        if GenericTypeRegistry.is_registered(name):
            return name

    # Case 2: Instance method call (instance.method())
    # Try to infer from resolved type on the expression
    if hasattr(expr, 'resolved_struct_type') and isinstance(expr.resolved_struct_type, StructType):
        return _extract_base_name(expr.resolved_struct_type.name)

    if hasattr(expr, 'resolved_enum_type') and isinstance(expr.resolved_enum_type, EnumType):
        return _extract_base_name(expr.resolved_enum_type.name)

    # Try to infer from receiver's semantic type
    if hasattr(receiver, 'semantic_type'):
        semantic_type = receiver.semantic_type
        if isinstance(semantic_type, (StructType, EnumType)):
            return _extract_base_name(semantic_type.name)

    return None


def _extract_base_name(type_name: str) -> Optional[str]:
    """Extract base name from a monomorphized generic type name.

    Examples:
        "HashMap<i32, string>" -> "HashMap"
        "List<i32>" -> "List"
        "Result<i32, StdError>" -> "Result"
        "i32" -> None

    Args:
        type_name: The full type name

    Returns:
        The base generic type name, or None if not a generic type
    """
    if '<' in type_name:
        base = type_name.split('<')[0]
        if GenericTypeRegistry.is_registered(base):
            return base
    return None


def _prepare_receiver(
    codegen: 'LLVMCodegen',
    expr: Union[MethodCall, DotCall],
    type_name: str,
    provider
) -> tuple[Optional[ir.Value], Optional[Union[StructType, EnumType]]]:
    """Prepare the receiver value and type for a method call.

    For static methods (e.g., HashMap.new()), returns (None, type).
    For instance methods, emits the receiver and returns (value, type).

    Args:
        codegen: The LLVM code generator
        expr: The method call expression
        type_name: The generic type name
        provider: The generic type provider

    Returns:
        Tuple of (receiver_value, receiver_type)
    """
    from backend.expressions.calls.utils import infer_semantic_type, emit_receiver_as_pointer

    method_specs = provider.get_method_specs()
    method_spec = method_specs.get(expr.method)

    # Check if this is a static method
    is_static = method_spec.is_static if method_spec else False
    is_mutating = method_spec.is_mutating if method_spec else False

    if is_static:
        # Static method: no receiver value needed
        # Infer type from expression context
        type_class = StructType if _is_struct_provider(provider) else EnumType
        prefix = f"{type_name}<"
        receiver_type = infer_semantic_type(codegen, expr, None, prefix, type_class)
        return None, receiver_type
    else:
        # Instance method: emit receiver
        if is_mutating:
            # Mutating method needs pointer to receiver
            receiver_ptr = emit_receiver_as_pointer(codegen, expr.receiver)
            if receiver_ptr is not None:
                type_class = StructType if _is_struct_provider(provider) else EnumType
                prefix = f"{type_name}<"
                receiver_type = infer_semantic_type(codegen, expr, None, prefix, type_class)
                return receiver_ptr, receiver_type

        # Non-mutating or couldn't get pointer: emit value
        receiver_value = codegen.expressions.emit_expr(expr.receiver)
        type_class = StructType if _is_struct_provider(provider) else EnumType
        prefix = f"{type_name}<"
        receiver_type = infer_semantic_type(codegen, expr, receiver_value, prefix, type_class)
        return receiver_value, receiver_type


def _is_struct_provider(provider) -> bool:
    """Check if a provider provides a struct type (vs enum type)."""
    from semantics.generics.types import GenericStructType
    type_def = provider.get_type_definition()
    return isinstance(type_def, GenericStructType)
