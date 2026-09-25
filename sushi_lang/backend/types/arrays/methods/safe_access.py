"""Safe array element access returning Maybe<T>."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType
from sushi_lang.backend import gep_utils
from sushi_lang.backend.types.arrays.bounds import emit_checked_maybe

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_fixed_array_get_maybe(
    codegen: 'LLVMCodegen',
    array_ptr: ir.Value,
    array_type: ir.ArrayType,
    index_value: ir.Value,
    semantic_type: 'Type',
    to_i1: bool
) -> ir.Value:
    """Emit code for fixed array .get() returning Maybe<T>."""
    from sushi_lang.semantics.typesys import deref_type

    actual_type = deref_type(semantic_type)

    if not isinstance(actual_type, ArrayType):
        from sushi_lang.internals.errors import raise_internal_error
        raise_internal_error("CE0042", type=type(semantic_type).__name__)
    element_semantic_type = actual_type.base_type

    array_size = ir.Constant(codegen.types.i32, array_type.count)
    zero = ir.Constant(codegen.types.i32, 0)

    # The receiver arrives as an address from `as_fixed_array_address` (#480), so the GEP
    # reads the owner rather than an `alloca`'d copy of it.
    def read_element() -> ir.Value:
        element_ptr = codegen.builder.gep(array_ptr, [zero, index_value], name="element_ptr")
        return codegen.builder.load(element_ptr, name="element")

    # `.get()` READS. It does not detach (#242): the array keeps the element and still
    # frees it, so the `Maybe.Some(...)` carries a BORROW. The borrow pass classifies it BORROWED,
    # a `let` of it binds without owning, and a position that takes ownership rejects it
    # (CE2411). `.pop()` is the one that still moves, because it removes the element.
    return emit_checked_maybe(codegen, index_value, array_size, element_semantic_type,
                              read_element)


def emit_dynamic_array_get_maybe(
    codegen: 'LLVMCodegen',
    array_value: ir.Value,
    index_value: ir.Value,
    semantic_type: 'Type',
    to_i1: bool
) -> ir.Value:
    """Emit code for dynamic array .get() returning Maybe<T>."""
    from sushi_lang.semantics.typesys import deref_type

    actual_type = deref_type(semantic_type)

    if not isinstance(actual_type, DynamicArrayType):
        from sushi_lang.internals.errors import raise_internal_error
        raise_internal_error("CE0042", type=type(semantic_type).__name__)
    element_semantic_type = actual_type.base_type

    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    current_len = codegen.builder.load(len_ptr, name="array_len")

    def read_element() -> ir.Value:
        data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)
        data_ptr = codegen.builder.load(data_ptr_ptr, name="array_data")
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
        return codegen.builder.load(element_ptr, name="element")

    # A BORROW, as in the fixed-array `.get()` above (#242).
    return emit_checked_maybe(codegen, index_value, current_len, element_semantic_type,
                              read_element)
