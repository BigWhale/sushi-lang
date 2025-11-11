"""
Safe array element access returning Maybe<T>.

This module implements .get() methods for both fixed and dynamic arrays
that return Maybe<T> instead of throwing runtime errors on out-of-bounds access.

Functions:
- emit_fixed_array_get_maybe: Fixed array .get() returning Maybe<T>
- emit_dynamic_array_get_maybe: Dynamic array .get() returning Maybe<T>

Note: Direct indexing arr[i] still throws RE2020 for out-of-bounds access.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from semantics.typesys import ArrayType, DynamicArrayType
from backend import gep_utils

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.typesys import Type


def _infer_semantic_type_from_ir(ir_type: ir.Type) -> 'Type':
    """Infer semantic type from LLVM IR type.

    This is used when semantic type information is not available (e.g., in string interpolation).

    Args:
        ir_type: The LLVM IR type.

    Returns:
        The inferred semantic type.
    """
    from semantics.typesys import BuiltinType

    # Map LLVM IR types to semantic types
    if isinstance(ir_type, ir.IntType):
        bit_width = ir_type.width
        if bit_width == 1:
            return BuiltinType.BOOL
        elif bit_width == 8:
            return BuiltinType.I8  # Default to signed for inference
        elif bit_width == 16:
            return BuiltinType.I16
        elif bit_width == 32:
            return BuiltinType.I32
        elif bit_width == 64:
            return BuiltinType.I64
    elif isinstance(ir_type, ir.PointerType):
        # Check if it's a string (i8*)
        if isinstance(ir_type.pointee, ir.IntType) and ir_type.pointee.width == 8:
            return BuiltinType.STRING

    # If we can't infer, return i32 as a reasonable default
    return BuiltinType.I32


def emit_fixed_array_get_maybe(
    codegen: 'LLVMCodegen',
    array_value: ir.Value,
    array_type: ir.ArrayType,
    index_value: ir.Value,
    semantic_type: 'Type',
    to_i1: bool
) -> ir.Value:
    """Emit code for fixed array .get() returning Maybe<T>.

    Performs runtime bounds checking and returns:
    - Maybe.Some(element) if index is in bounds
    - Maybe.None() if index is out of bounds

    Args:
        codegen: The LLVM codegen instance.
        array_value: The fixed array value (loaded value, not pointer).
        array_type: The LLVM array type.
        index_value: The index to access (i32).
        semantic_type: The semantic type of the array (FixedArrayType).
        to_i1: Whether to convert result to i1 (ignored for Maybe).

    Returns:
        Maybe<T> enum value.
    """
    from backend.generics.maybe import emit_maybe_some, emit_maybe_none
    from semantics.typesys import ReferenceType

    # Extract element type from semantic type
    # Handle references to arrays (e.g., &i32[])
    actual_type = semantic_type.referenced_type if isinstance(semantic_type, ReferenceType) else semantic_type

    if isinstance(actual_type, ArrayType):
        element_semantic_type = actual_type.base_type
    elif semantic_type is None:
        # When semantic_type is None (e.g., in string interpolation),
        # infer element type from the LLVM array type
        element_ir_type = array_type.element
        element_semantic_type = _infer_semantic_type_from_ir(element_ir_type)
    else:
        from internals.errors import raise_internal_error
        raise_internal_error("CE0042", type=type(semantic_type).__name__)

    # Get array size
    array_size = ir.Constant(codegen.types.i32, array_type.count)
    zero = ir.Constant(codegen.types.i32, 0)

    # Runtime bounds checking: index >= 0 && index < len
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero, name="index_not_negative")
    index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, array_size, name="index_in_bounds")
    bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

    # Create basic blocks for bounds check
    bounds_ok_block = codegen.func.append_basic_block("get_bounds_ok")
    bounds_fail_block = codegen.func.append_basic_block("get_bounds_fail")
    merge_block = codegen.func.append_basic_block("get_merge")

    # Branch based on bounds check
    codegen.builder.cbranch(bounds_ok, bounds_ok_block, bounds_fail_block)

    # Bounds OK block: return Maybe.Some(element)
    codegen.builder.position_at_end(bounds_ok_block)

    # Need to get pointer to array for GEP
    # If array_value is already loaded, we need to store it temporarily
    array_temp = codegen.builder.alloca(array_type, name="array_temp")
    codegen.builder.store(array_value, array_temp)

    # Access element using GEP
    element_ptr = codegen.builder.gep(array_temp, [zero, index_value], name="element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element")

    # Wrap in Maybe.Some
    some_result = emit_maybe_some(codegen, element_semantic_type, element_value)
    codegen.builder.branch(merge_block)

    # Bounds fail block: return Maybe.None()
    codegen.builder.position_at_end(bounds_fail_block)
    none_result = emit_maybe_none(codegen, element_semantic_type)
    codegen.builder.branch(merge_block)

    # Merge block: phi node to select result
    codegen.builder.position_at_end(merge_block)
    result_phi = codegen.builder.phi(some_result.type, name="get_result")
    result_phi.add_incoming(some_result, bounds_ok_block)
    result_phi.add_incoming(none_result, bounds_fail_block)

    return result_phi


def emit_dynamic_array_get_maybe(
    codegen: 'LLVMCodegen',
    array_value: ir.Value,
    array_type: ir.LiteralStructType,
    index_value: ir.Value,
    semantic_type: 'Type',
    to_i1: bool
) -> ir.Value:
    """Emit code for dynamic array .get() returning Maybe<T>.

    Performs runtime bounds checking and returns:
    - Maybe.Some(element) if index is in bounds
    - Maybe.None() if index is out of bounds

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.
        index_value: The index to access (i32).
        semantic_type: The semantic type of the array (DynamicArrayType).
        to_i1: Whether to convert result to i1 (ignored for Maybe).

    Returns:
        Maybe<T> enum value.
    """
    from backend.generics.maybe import emit_maybe_some, emit_maybe_none
    from semantics.typesys import ReferenceType

    # Extract element type from semantic type
    # Handle references to arrays (e.g., &i32[])
    actual_type = semantic_type.referenced_type if isinstance(semantic_type, ReferenceType) else semantic_type

    if isinstance(actual_type, DynamicArrayType):
        element_semantic_type = actual_type.base_type
    elif semantic_type is None:
        # When semantic_type is None (e.g., in string interpolation),
        # infer element type from the LLVM array struct type
        # array_type.elements[0] is the data pointer (e.g., i32*)
        # We need to get its pointee to get the element type (e.g., i32)
        data_ptr_type = array_type.elements[0]
        if isinstance(data_ptr_type, ir.PointerType):
            element_ir_type = data_ptr_type.pointee
        else:
            element_ir_type = data_ptr_type
        element_semantic_type = _infer_semantic_type_from_ir(element_ir_type)
    else:
        from internals.errors import raise_internal_error
        raise_internal_error("CE0042", type=type(semantic_type).__name__)

    # Get current array length for bounds checking
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    current_len = codegen.builder.load(len_ptr, name="array_len")

    # Runtime bounds checking: index >= 0 && index < len
    zero = ir.Constant(codegen.types.i32, 0)
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero, name="index_not_negative")
    index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, current_len, name="index_in_bounds")
    bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

    # Create basic blocks for bounds check
    bounds_ok_block = codegen.func.append_basic_block("get_bounds_ok")
    bounds_fail_block = codegen.func.append_basic_block("get_bounds_fail")
    merge_block = codegen.func.append_basic_block("get_merge")

    # Branch based on bounds check
    codegen.builder.cbranch(bounds_ok, bounds_ok_block, bounds_fail_block)

    # Bounds OK block: return Maybe.Some(element)
    codegen.builder.position_at_end(bounds_ok_block)

    # Get data pointer and access element
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)
    data_ptr = codegen.builder.load(data_ptr_ptr, name="array_data")

    # Use GEP to get element pointer
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")

    # Load element value
    element_value = codegen.builder.load(element_ptr, name="element")

    # Wrap in Maybe.Some
    some_result = emit_maybe_some(codegen, element_semantic_type, element_value)
    codegen.builder.branch(merge_block)

    # Bounds fail block: return Maybe.None()
    codegen.builder.position_at_end(bounds_fail_block)
    none_result = emit_maybe_none(codegen, element_semantic_type)
    codegen.builder.branch(merge_block)

    # Merge block: phi node to select result
    codegen.builder.position_at_end(merge_block)
    result_phi = codegen.builder.phi(some_result.type, name="get_result")
    result_phi.add_incoming(some_result, bounds_ok_block)
    result_phi.add_incoming(none_result, bounds_fail_block)

    return result_phi
