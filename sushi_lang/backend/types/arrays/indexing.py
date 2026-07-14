"""
Array indexing operations with bounds checking.

This module handles LLVM IR emission for array element access (array[index]).
Includes runtime bounds checking with error RE2020 for out-of-bounds access.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import IndexAccess, Name
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_index_access(codegen: 'LLVMCodegen', expr: IndexAccess, to_i1: bool = False) -> ir.Value:
    """Emit array indexing operation using GEP instruction.

    Performs array element access with runtime bounds checking for fixed arrays.
    Emits runtime error RE2020 if index is out of bounds.

    Args:
        codegen: The LLVM codegen instance.
        expr: The index access expression.
        to_i1: Whether to convert result to i1 for boolean contexts.

    Returns:
        The value at the specified array index.

    Note:
        Emits runtime error RE2020 for out-of-bounds access on fixed arrays.
    """
    element_ptr = emit_element_pointer(codegen, expr)

    # Load the value from the pointer
    result = codegen.builder.load(element_ptr)
    return _finish_index_access(codegen, expr, result, to_i1)


def emit_element_pointer(codegen: 'LLVMCodegen', expr: IndexAccess) -> ir.Value:
    """Emit the bounds-checked POINTER to `expr`'s element, without loading it.

    Split out of `emit_index_access` so a field read through an index (`a[i].field`) can GEP
    straight into the element (#187) instead of loading it to a value first: a dynamic-array
    field must be reached by pointer, because `.len()`/`.push()` dispatch on the field's
    address, not on a copy of it.
    """
    from sushi_lang.backend.expressions import type_utils

    builder = require_builder(codegen)
    # For array indexing, we need to get the array slot directly from the variable
    # rather than loading the array value
    if isinstance(expr.array, Name):
        # Get the array slot directly from memory manager
        array_slot = codegen.memory.find_local_slot(expr.array.id)

        # For reference parameters, the slot contains a pointer to the actual array
        # We need to load that pointer to get the array's address
        if type_utils.is_reference_parameter(codegen, expr.array.id):
            array_slot = codegen.builder.load(array_slot, name=f"{expr.array.id}_ref_ptr")
    else:
        # For more complex array expressions, emit normally
        array_value = codegen.expressions.emit_expr(expr.array)
        array_slot = array_value

    # Emit the index expression (should be an integer)
    index_value = codegen.expressions.emit_expr(expr.index)

    # Compile-time constant checking: detect negative or out-of-bounds constant indices
    if isinstance(index_value, ir.Constant):
        const_index = index_value.constant
        # Check for negative index
        if const_index < 0:
            raise_internal_error("CE2056", index=const_index)
        # For fixed arrays, check if index is out of bounds at compile time
        array_type = array_slot.type.pointee
        if isinstance(array_type, ir.ArrayType):
            array_size = array_type.count
            if const_index >= array_size:
                raise_internal_error("CE2057", index=const_index, size=array_size)

    # Add runtime bounds checking. Both fixed and dynamic arrays trap RE2020 on
    # an out-of-bounds direct index; the difference is only where the size comes
    # from (a compile-time count vs. a loaded length field).
    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.types.arrays.bounds import emit_bounds_check

    array_type = array_slot.type.pointee
    if isinstance(array_type, ir.ArrayType):
        size_value = ir.Constant(codegen.i32, array_type.count)
        emit_bounds_check(codegen, index_value, size_value, prefix="array")
    elif isinstance(array_type, ir.LiteralStructType):
        len_ptr = gep_utils.gep_dynamic_array_len(codegen, array_slot, "len_ptr")
        size_value = codegen.builder.load(len_ptr, name="array_len")
        emit_bounds_check(codegen, index_value, size_value, prefix="dynarray")

    # Use GEP to get pointer to the array element
    # llvmlite's GEP validation requires constant indices for structs and arrays
    # Workaround: Convert to element pointer first, then use single-index GEP
    zero = ir.Constant(codegen.i32, 0)

    if isinstance(array_type, ir.ArrayType):
        # Fixed array: Get pointer to first element, then use pointer arithmetic
        # This avoids llvmlite's .constant validation for the second index
        first_elem_ptr = codegen.builder.gep(array_slot, [zero, zero], name="first_elem")
        element_ptr = gep_utils.gep_array_element(codegen, first_elem_ptr, index_value, "elem_ptr")
    elif isinstance(array_type, ir.LiteralStructType):
        # Dynamic array struct: Extract data pointer, then use pointer arithmetic
        # This avoids llvmlite's .constant validation for struct field indices
        data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, array_slot, "data_ptr")
        data_ptr = codegen.builder.load(data_ptr_ptr, name="array_data")
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "elem_ptr")
    else:
        # Other pointer types (shouldn't happen for array indexing)
        element_ptr = codegen.builder.gep(array_slot, [zero, index_value])

    return element_ptr


def _finish_index_access(codegen: 'LLVMCodegen', expr: IndexAccess, result: ir.Value,
                         to_i1: bool) -> ir.Value:
    """Apply value semantics to a loaded element and coerce for a boolean context."""
    # Value semantics (#60, #145): an element that owns heap memory must be deep-copied so
    # the indexed copy does not shallow-share the array element's buffer. Two owners of one
    # buffer (the array's element destructor and the new binding / the container it is stored
    # into) would otherwise double-free. Covers owning structs/enums AND heap-owned string
    # elements (`let s = words[0]`, `m.insert(words[0], ...)` on a split() array, N1). Only the
    # Name-array case carries a resolvable element type; other forms are left unchanged.
    if not to_i1 and isinstance(expr.array, Name):
        from sushi_lang.semantics.typesys import (
            ArrayType, DynamicArrayType, ReferenceType, BuiltinType)
        array_sem = codegen.variable_types.get(expr.array.id) or codegen.memory.find_semantic_type(expr.array.id)
        if isinstance(array_sem, ReferenceType):
            array_sem = array_sem.referenced_type
        if isinstance(array_sem, (ArrayType, DynamicArrayType)):
            from sushi_lang.backend.expressions import memory
            if array_sem.base_type == BuiltinType.STRING:
                # A string element clones its heap buffer (owned=1) so a `let` binding or a
                # container the value is stored into becomes the sole owner of an independent
                # buffer, while the array keeps and frees the original element. A literal
                # element (owned=0) clones to another owned=0, whose free is a no-op.
                result = memory.emit_value_clone(codegen, result, array_sem.base_type)
                # A transient index-load with no binding (e.g. println(words[0])) would leak
                # this clone; register it for an owned-bit-guarded free at the end of the
                # print-arg frame. Outside a print frame (a `let`/container store) this is a
                # no-op and the new owner frees the clone.
                codegen.register_string_value_temp(result)
            else:
                result = memory.deep_copy_if_owning_struct(codegen, result, array_sem.base_type)

    return codegen.utils.as_i1(result) if to_i1 else result
