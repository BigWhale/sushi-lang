"""Array indexing operations with bounds checking."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import IndexAccess, Name, MemberAccess
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_index_access(codegen: 'LLVMCodegen', expr: IndexAccess, to_i1: bool = False) -> ir.Value:
    """Emit array indexing operation using GEP instruction."""
    element_ptr = emit_element_pointer(codegen, expr)

    result = codegen.builder.load(element_ptr)
    return _finish_index_access(codegen, expr, result, to_i1)


def emit_element_pointer(codegen: 'LLVMCodegen', expr: IndexAccess) -> ir.Value:
    """Emit the bounds-checked POINTER to `expr`'s element, without loading it."""
    from sushi_lang.backend.expressions import type_utils

    require_builder(codegen)
    if isinstance(expr.array, Name):
        # Local alloca, or the global backing an array constant (#248) -- indexing a
        # constant directly used to be a CE0000 ICE because this consulted only locals.
        from sushi_lang.backend.expressions.names import resolve_name_slot
        array_slot = resolve_name_slot(codegen, expr.array.id)
        if array_slot is None:
            raise_internal_error("CE0055", name=expr.array.id)

        if type_utils.is_reference_parameter(codegen, expr.array.id):
            array_slot = codegen.builder.load(array_slot, name=f"{expr.array.id}_ref_ptr")
    elif isinstance(expr.array, MemberAccess):
        # An array that is a STRUCT FIELD is indexed through its ADDRESS (#200): emitting it
        # as an expression hands back a fixed array by VALUE, and everything below wants a
        # pointer. Only the fixed case broke -- a dynamic-array field already came back as
        # a pointer.
        from sushi_lang.backend.expressions.structs import try_get_struct_alloca
        field_ptr = try_get_struct_alloca(codegen, expr.array)
        array_slot = (field_ptr if field_ptr is not None
                      else codegen.expressions.emit_expr(expr.array))
    else:
        array_value = codegen.expressions.emit_expr(expr.array)
        array_slot = array_value

    index_value = codegen.expressions.emit_expr(expr.index)

    if isinstance(index_value, ir.Constant):
        const_index = index_value.constant
        if const_index < 0:
            raise_internal_error("CE2056", index=const_index)
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
    # The LiteralStructType arms here and in the element-GEP below stay literal on purpose
    # (#257). This is a two-way discrimination between a FIXED array (ir.ArrayType) and a
    # DYNAMIC array's anonymous {i32, i32, T*} descriptor -- the only two things an indexable
    # slot can hold. A user struct is never indexed with `[]`, and since #257 it is an
    # identified type, so it cannot reach either arm by shape coincidence.
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
        element_ptr = codegen.builder.gep(array_slot, [zero, index_value])

    return element_ptr


def _finish_index_access(codegen: 'LLVMCodegen', expr: IndexAccess, result: ir.Value,
                         to_i1: bool) -> ir.Value:
    """Coerce a loaded element for a boolean context. A read never detaches."""
    return codegen.utils.as_i1(result) if to_i1 else result
