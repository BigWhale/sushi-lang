"""Safe array element access returning Maybe<T>."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType
from sushi_lang.backend import gep_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def _infer_semantic_type_from_ir(ir_type: ir.Type) -> 'Type':
    """Infer semantic type from LLVM IR type."""
    from sushi_lang.semantics.typesys import BuiltinType

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
    """Emit code for fixed array .get() returning Maybe<T>."""
    from sushi_lang.backend.generics.maybe import emit_maybe_some, emit_maybe_none
    from sushi_lang.semantics.typesys import deref_type

    # Extract element type from semantic type
    # Handle references to arrays (e.g., &i32[])
    actual_type = deref_type(semantic_type)

    if isinstance(actual_type, ArrayType):
        element_semantic_type = actual_type.base_type
    elif semantic_type is None:
        # When semantic_type is None (e.g., in string interpolation),
        # infer element type from the LLVM array type
        element_ir_type = array_type.element
        element_semantic_type = _infer_semantic_type_from_ir(element_ir_type)
    else:
        from sushi_lang.internals.errors import raise_internal_error
        raise_internal_error("CE0042", type=type(semantic_type).__name__)

    # Get array size
    array_size = ir.Constant(codegen.types.i32, array_type.count)
    zero = ir.Constant(codegen.types.i32, 0)

    # Out-of-bounds returns Maybe.None() instead of trapping.
    from sushi_lang.backend.types.arrays.bounds import emit_bounds_check
    merge_block = codegen.func.append_basic_block("get_merge")
    none_state: dict = {}

    def on_fail() -> None:
        none_state["result"] = emit_maybe_none(codegen, element_semantic_type)
        none_state["pred"] = codegen.builder.block
        codegen.builder.branch(merge_block)

    emit_bounds_check(codegen, index_value, array_size, prefix="get", on_fail=on_fail)

    # Bounds OK block: return Maybe.Some(element)
    # Need to get pointer to array for GEP
    # If array_value is already loaded, we need to store it temporarily
    array_temp = codegen.builder.alloca(array_type, name="array_temp")
    codegen.builder.store(array_value, array_temp)

    # Access element using GEP
    element_ptr = codegen.builder.gep(array_temp, [zero, index_value], name="element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element")

    # `.get()` READS. It does not detach (#242): the array keeps the element and still
    # frees it, so the `Maybe.Some(...)` carries a BORROW. Pass 3 classifies it BORROWED,
    # a `let` of it binds without owning, and a position that takes ownership rejects it
    # (CE2411). `.pop()` is the one that still moves, because it removes the element.

    # Wrap in Maybe.Some
    some_result = emit_maybe_some(codegen, element_semantic_type, element_value)
    # Capture the actual predecessor for the phi: the deep copy / Maybe.Some emission may
    # have inserted basic blocks, so the live block is no longer the ok-block.
    some_pred_block = codegen.builder.block
    codegen.builder.branch(merge_block)

    # Merge block: phi node to select result
    codegen.builder.position_at_end(merge_block)
    result_phi = codegen.builder.phi(some_result.type, name="get_result")
    result_phi.add_incoming(some_result, some_pred_block)
    result_phi.add_incoming(none_state["result"], none_state["pred"])

    return result_phi


def emit_dynamic_array_get_maybe(
    codegen: 'LLVMCodegen',
    array_value: ir.Value,
    array_type: ir.LiteralStructType,
    index_value: ir.Value,
    semantic_type: 'Type',
    to_i1: bool
) -> ir.Value:
    """Emit code for dynamic array .get() returning Maybe<T>."""
    from sushi_lang.backend.generics.maybe import emit_maybe_some, emit_maybe_none
    from sushi_lang.semantics.typesys import deref_type

    # Extract element type from semantic type
    # Handle references to arrays (e.g., &i32[])
    actual_type = deref_type(semantic_type)

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
        from sushi_lang.internals.errors import raise_internal_error
        raise_internal_error("CE0042", type=type(semantic_type).__name__)

    # Get current array length for bounds checking
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    current_len = codegen.builder.load(len_ptr, name="array_len")

    # Out-of-bounds returns Maybe.None() instead of trapping.
    from sushi_lang.backend.types.arrays.bounds import emit_bounds_check
    merge_block = codegen.func.append_basic_block("get_merge")
    none_state: dict = {}

    def on_fail() -> None:
        none_state["result"] = emit_maybe_none(codegen, element_semantic_type)
        none_state["pred"] = codegen.builder.block
        codegen.builder.branch(merge_block)

    emit_bounds_check(codegen, index_value, current_len, prefix="get", on_fail=on_fail)

    # Bounds OK block: return Maybe.Some(element)
    # Get data pointer and access element
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)
    data_ptr = codegen.builder.load(data_ptr_ptr, name="array_data")

    # Use GEP to get element pointer
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")

    # Load element value
    element_value = codegen.builder.load(element_ptr, name="element")

    # `.get()` READS. It does not detach (#242): the array keeps the element and still
    # frees it, so the `Maybe.Some(...)` carries a BORROW. Pass 3 classifies it BORROWED,
    # a `let` of it binds without owning, and a position that takes ownership rejects it
    # (CE2411). `.pop()` is the one that still moves, because it removes the element.

    # Wrap in Maybe.Some
    some_result = emit_maybe_some(codegen, element_semantic_type, element_value)
    # Capture the actual predecessor for the phi: the deep copy / Maybe.Some emission may
    # have inserted basic blocks, so the live block is no longer the ok-block.
    some_pred_block = codegen.builder.block
    codegen.builder.branch(merge_block)

    # Merge block: phi node to select result
    codegen.builder.position_at_end(merge_block)
    result_phi = codegen.builder.phi(some_result.type, name="get_result")
    result_phi.add_incoming(some_result, some_pred_block)
    result_phi.add_incoming(none_state["result"], none_state["pred"])

    return result_phi
