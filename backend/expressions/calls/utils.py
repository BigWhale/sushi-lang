"""
Utility functions for method call emission.

This module contains helper functions for type inference, receiver emission,
and generic type resolution used by the method call dispatcher.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Tuple, Union

from llvmlite import ir
from semantics.ast import Name, Call, Expr, MemberAccess, MethodCall, DotCall
from semantics.typesys import EnumType, StructType

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.typesys import Type


def infer_generic_struct_type(codegen: 'LLVMCodegen', receiver: Expr, prefix: str) -> Optional[StructType]:
    """Infer generic struct type (Own<T>, HashMap<K,V>, List<T>) from receiver using multiple strategies."""
    from semantics.typesys import ReferenceType
    from semantics.generics.types import GenericTypeRef

    # Strategy 1: Check if receiver is a Name (variable)
    if isinstance(receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(receiver.id)
        # Unwrap ReferenceType if present (for &peek T or &poke T parameters)
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type

        if isinstance(semantic_type, StructType) and semantic_type.name.startswith(prefix):
            return semantic_type

        # Handle GenericTypeRef (e.g., HashMap<string, string> before resolution)
        # Resolve it to the actual StructType from the struct table
        if isinstance(semantic_type, GenericTypeRef):
            type_name = str(semantic_type)  # e.g., "HashMap<string, string>"
            if type_name.startswith(prefix) and type_name in codegen.struct_table.by_name:
                return codegen.struct_table.by_name[type_name]

    # Strategy 2: For Own.new(), receiver might be a type name from let statement type annotation
    # This would be handled during type inference in semantic analysis

    return None


def infer_generic_enum_type(codegen: 'LLVMCodegen', receiver: Expr, receiver_value: ir.Value, prefix: str) -> Optional[EnumType]:
    """Infer generic enum type (Result<T> or Maybe<T>) from receiver using multiple strategies."""
    from semantics.typesys import ReferenceType
    from semantics.generics.types import GenericTypeRef

    # Strategy 1: Check if receiver is a Name (variable)
    if isinstance(receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(receiver.id)
        # Unwrap ReferenceType if present (for &peek T or &poke T parameters)
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type

        if isinstance(semantic_type, EnumType) and semantic_type.name.startswith(prefix):
            return semantic_type

        # Handle GenericTypeRef (e.g., Result<i32> before resolution)
        # Resolve it to the actual EnumType from the enum table
        if isinstance(semantic_type, GenericTypeRef):
            type_name = str(semantic_type)  # e.g., "Result<i32>"
            if type_name.startswith(prefix) and type_name in codegen.enum_table.by_name:
                return codegen.enum_table.by_name[type_name]

    # Strategy 2: Infer from function call return type (for Call expressions)
    if isinstance(receiver, Call):
        func_name = receiver.callee.id
        if func_name in codegen.function_return_types:
            result_type = codegen.function_return_types[func_name]
            # Handle ResultType wrapper
            from semantics.typesys import ResultType
            if isinstance(result_type, ResultType):
                result_enum_name = f"Result<{result_type.ok_type}>"
                if result_enum_name in codegen.enum_table.by_name:
                    return codegen.enum_table.by_name[result_enum_name]
            # Handle direct EnumType
            elif isinstance(result_type, EnumType) and result_type.name.startswith(prefix):
                return result_type

    # Strategy 3: Fallback to LLVM type matching
    for enum_name, enum_type in codegen.enum_table.by_name.items():
        if isinstance(enum_type, EnumType) and enum_name.startswith(prefix):
            expected_llvm_type = codegen.types.ll_type(enum_type)
            if receiver_value.type == expected_llvm_type:
                return enum_type

    return None


def emit_receiver_value(codegen: 'LLVMCodegen', receiver: Expr) -> Tuple[ir.Value, ir.Type, Optional['Type']]:
    """Emit receiver value with special handling for dynamic arrays and references.

    Returns tuple of (receiver_value, receiver_type, semantic_type).
    """
    from backend.expressions import type_utils

    semantic_type = None

    if isinstance(receiver, Name):
        slot = codegen.memory.find_local_slot(receiver.id)
        slot_type = slot.type.pointee
        semantic_type = codegen.memory.find_semantic_type(receiver.id)

        # Check if this is a reference parameter
        if type_utils.is_reference_parameter(codegen, receiver.id):
            receiver_value = codegen.builder.load(slot, name=f"{receiver.id}_ref")
            receiver_type = receiver_value.type
        elif codegen.types.is_dynamic_array_type(slot_type):
            receiver_value = slot  # Use the alloca pointer directly
            receiver_type = slot_type
        else:
            receiver_value = codegen.expressions.emit_expr(receiver)
            receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)
    elif isinstance(receiver, MemberAccess):
        # _emit_member_access() already returns a pointer for dynamic array fields
        receiver_value = codegen.expressions.emit_expr(receiver)
        receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)
        # Extract semantic type from struct field
        from backend.expressions.structs import infer_struct_type
        try:
            struct_type = infer_struct_type(codegen, receiver.receiver)
            semantic_type = struct_type.get_field_type(receiver.member)
        except:
            # If we can't infer the struct type, leave semantic_type as None
            pass
    else:
        receiver_value = codegen.expressions.emit_expr(receiver)
        receiver_type = codegen.types.infer_llvm_type_from_value(receiver_value)

    return receiver_value, receiver_type, semantic_type


def get_resolved_type(expr: Union[MethodCall, DotCall], type_attr: str) -> Optional['Type']:
    """Extract resolved type from expr if present.

    Args:
        expr: Method call or DotCall expression
        type_attr: Attribute name ('resolved_enum_type' or 'resolved_struct_type')

    Returns:
        Resolved type if present, None otherwise
    """
    if hasattr(expr, type_attr):
        resolved_type = getattr(expr, type_attr)
        if resolved_type is not None:
            return resolved_type
    return None


def infer_semantic_type(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                        receiver_value: Optional[ir.Value], expected_prefix: str,
                        expected_type_class) -> Optional['Type']:
    """Unified type inference for generic types.

    This function unifies the type inference pattern repeated across all generic type handlers.
    It tries multiple strategies in priority order:
    1. Check for resolved type annotation (resolved_enum_type or resolved_struct_type)
    2. Call appropriate inference function based on type class

    Args:
        codegen: The LLVM code generator
        expr: Method call or DotCall expression
        receiver_value: Emitted receiver value (None if not yet emitted)
        expected_prefix: Type name prefix (e.g., "Result<", "Maybe<", "Own<", "HashMap<", "List<")
        expected_type_class: Expected type class (EnumType or StructType)

    Returns:
        Inferred type if successful, None otherwise
    """
    receiver = expr.receiver

    # Priority 1: Check for resolved type annotation
    if expected_type_class == EnumType:
        resolved_type = get_resolved_type(expr, 'resolved_enum_type')
        if resolved_type is not None:
            return resolved_type
    elif expected_type_class == StructType:
        resolved_type = get_resolved_type(expr, 'resolved_struct_type')
        if resolved_type is not None:
            return resolved_type

    # Priority 2: Use appropriate inference function
    if expected_type_class == EnumType:
        if receiver_value is None:
            # Need to emit receiver first
            receiver_value = codegen.expressions.emit_expr(receiver)
        return infer_generic_enum_type(codegen, receiver, receiver_value, expected_prefix)
    elif expected_type_class == StructType:
        return infer_generic_struct_type(codegen, receiver, expected_prefix)

    return None


def emit_receiver_as_pointer(codegen: 'LLVMCodegen', receiver: Expr) -> Optional[ir.Value]:
    """Emit receiver as pointer (alloca) for mutation methods.

    This is used by HashMap and List methods that need to mutate the receiver.

    For reference parameters (&peek T or &poke T), the slot contains a pointer
    to the actual variable, so we need to load that pointer first.

    Args:
        codegen: The LLVM code generator
        receiver: Receiver expression

    Returns:
        Pointer to receiver if receiver is a Name, None otherwise
    """
    from backend.expressions import type_utils

    if isinstance(receiver, Name):
        slot = codegen.memory.find_local_slot(receiver.id)
        # Check if this is a reference parameter - if so, load the pointer
        if type_utils.is_reference_parameter(codegen, receiver.id):
            return codegen.builder.load(slot, name=f"{receiver.id}_ref_ptr")
        return slot
    return None
