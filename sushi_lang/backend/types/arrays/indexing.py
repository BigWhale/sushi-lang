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

    def address_of_value() -> ir.Value:
        # `emit_expr` yields an array by VALUE (docs/design/array-representation.md);
        # `as_array_address` is the one seam that turns it into an address.
        from .addressing import as_array_address
        from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
        array_value = codegen.expressions.emit_expr(expr.array)
        return as_array_address(codegen, array_value, None, expr.array,
                                infer_expr_semantic_type(codegen, expr.array))

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
        # No alloca arm matches a CHAINED receiver (`o.get().items[0]`, the #407 read
        # face): the field arrives by value and dies at `.type.pointee` unless it goes
        # through the same seam as the else-arm.
        array_slot = field_ptr if field_ptr is not None else address_of_value()
    else:
        # A chained or temporary array (`o.get()[0]`, `from([1, 2])[0]`).
        array_slot = address_of_value()

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

    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.types.arrays.bounds import emit_bounds_check

    # An indexable slot holds a FIXED array or a DYNAMIC array's anonymous {i32, i32, T*}
    # descriptor, and nothing else. A user struct is an identified type since #257, so it
    # cannot match the literal arm by shape. The base pointer is emitted after the check.
    array_type = array_slot.type.pointee
    match array_type:
        case ir.ArrayType():
            size_value: ir.Value = ir.Constant(codegen.i32, array_type.count)
            prefix, base_of = "array", _fixed_array_base
        case ir.LiteralStructType():
            len_ptr = gep_utils.gep_dynamic_array_len(codegen, array_slot, "len_ptr")
            size_value = codegen.builder.load(len_ptr, name="array_len")
            prefix, base_of = "dynarray", _dynamic_array_base
        case _:
            raise_internal_error("CE0022", type=str(array_type))

    emit_bounds_check(codegen, index_value, size_value, prefix=prefix)
    # A single-index GEP off the base: llvmlite wants a constant index into an aggregate.
    return gep_utils.gep_array_element(codegen, base_of(codegen, array_slot), index_value,
                                       "elem_ptr")


def _fixed_array_base(codegen: 'LLVMCodegen', array_slot: ir.Value) -> ir.Value:
    zero = ir.Constant(codegen.i32, 0)
    return codegen.builder.gep(array_slot, [zero, zero], name="first_elem")


def _dynamic_array_base(codegen: 'LLVMCodegen', array_slot: ir.Value) -> ir.Value:
    from sushi_lang.backend import gep_utils
    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, array_slot, "data_ptr")
    return codegen.builder.load(data_ptr_ptr, name="array_data")


def _finish_index_access(codegen: 'LLVMCodegen', expr: IndexAccess, result: ir.Value,
                         to_i1: bool) -> ir.Value:
    """Coerce a loaded element for a boolean context. A read never detaches."""
    return codegen.utils.as_i1(result) if to_i1 else result
