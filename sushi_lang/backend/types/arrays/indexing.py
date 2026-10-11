"""Array indexing operations with bounds checking."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from llvmlite import ir
from sushi_lang.semantics.ast import IndexAccess, Name, MemberAccess
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_index_access(codegen: 'LLVMCodegen', expr: IndexAccess, to_i1: bool = False) -> ir.Value:
    """Emit array indexing operation using GEP instruction."""
    if expr.is_slice:
        slot = emit_slice_address(codegen, expr)
        return require_builder(codegen).load(slot, name="slice")
    if expr.reads_a_string_byte:
        return _finish_index_access(codegen, expr, _emit_string_byte(codegen, expr), to_i1)
    element_ptr = emit_element_pointer(codegen, expr)

    result = codegen.builder.load(element_ptr)
    return _finish_index_access(codegen, expr, result, to_i1)


def _emit_string_byte(codegen: 'LLVMCodegen', expr: IndexAccess) -> ir.Value:
    """`s[i]` (#1091): the byte at offset i of the string's data. It is bounds-checked
    unless the index carries the `dont_panic` stamp.

    A read in place. A temporary string gets an owner, so `"{n}"[0]` frees it.
    """
    from sushi_lang.backend.expressions.memory import own_temporary
    from sushi_lang.backend.types.arrays.bounds import emit_bounds_check
    from sushi_lang.semantics.typesys import BuiltinType

    builder = require_builder(codegen)
    string = codegen.expressions.emit_expr(expr.array)
    own_temporary(codegen, expr.array, string, BuiltinType.STRING)
    index = codegen.utils.require_i32(codegen.expressions.emit_expr(expr.index))
    if not expr.unchecked:
        emit_bounds_check(codegen, index, builder.extract_value(string, 1), prefix="string")
    data = builder.extract_value(string, 0)
    return builder.load(builder.gep(data, [index], name="string_byte_at"), name="string_byte")


def emit_element_pointer(codegen: 'LLVMCodegen', expr: IndexAccess) -> ir.Value:
    """Emit the POINTER to `expr`'s element, without loading it. It is bounds-checked
    unless the index carries the `dont_panic` stamp.

    A slice has no element of the array to point at: its value is a new descriptor, and
    the pointer is the address of the slot that holds it.
    """
    if expr.is_slice:
        return emit_slice_address(codegen, expr)

    array_slot = _array_slot(codegen, expr)
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

    size_value, base_of, prefix = _size_and_base(codegen, array_slot)
    if not expr.unchecked:
        emit_bounds_check(codegen, index_value, size_value, prefix=prefix)
    # A single-index GEP off the base: llvmlite wants a constant index into an aggregate.
    return gep_utils.gep_array_element(codegen, base_of(codegen, array_slot), index_value,
                                       "elem_ptr")


def emit_slice_address(codegen: 'LLVMCodegen', expr: IndexAccess) -> ir.Value:
    """`data[a..b]`: the descriptor `{b - a, b - a, data + a}` in a slot (#1226).

    The range is checked first, `0 <= a <= b <= len()`, else RE2028. The check stays in a
    `dont_panic` body (S7). The slot owns nothing: the array keeps its storage, and the
    borrow pass gives the slice BORROWED provenance, so no scope frees it.
    """
    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.memory.allocas import entry_alloca
    from sushi_lang.backend.types.arrays.bounds import emit_slice_check
    from sushi_lang.semantics.ast import RangeExpr

    builder = require_builder(codegen)
    rng = expr.index
    if not isinstance(rng, RangeExpr):
        raise_internal_error("CE0100", expr=type(rng).__name__)
    array_slot = _array_slot(codegen, expr)
    start = codegen.utils.require_i32(codegen.expressions.emit_expr(rng.start))
    end = codegen.utils.require_i32(codegen.expressions.emit_expr(rng.end))
    if rng.inclusive:
        end = builder.add(end, ir.Constant(codegen.i32, 1), name="slice_end")
    size_value, base_of, _ = _size_and_base(codegen, array_slot)
    emit_slice_check(codegen, start, end, size_value)

    length = builder.sub(end, start, name="slice_len")
    data = gep_utils.gep_array_element(codegen, base_of(codegen, array_slot), start,
                                       "slice_data")
    descriptor = ir.LiteralStructType([codegen.i32, codegen.i32, data.type])
    slot = entry_alloca(builder, descriptor, name="slice_slot")
    builder.store(length, gep_utils.gep_dynamic_array_len(codegen, slot))
    builder.store(length, gep_utils.gep_dynamic_array_cap(codegen, slot))
    builder.store(data, gep_utils.gep_dynamic_array_data(codegen, slot))
    return slot


def _array_slot(codegen: 'LLVMCodegen', expr: IndexAccess) -> ir.Value:
    """The address of the array `expr` indexes: a fixed array or a `T[]` descriptor."""
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
    elif isinstance(expr.array, IndexAccess):
        # An element that is itself an array (`a[i][j]`) has storage of its own: its
        # address, never a spilled copy that a write or a growth would land on.
        array_slot = emit_element_pointer(codegen, expr.array)
    else:
        # A chained or temporary array (`o.get()[0]`, `from([1, 2])[0]`).
        array_slot = address_of_value()
    return array_slot


_BaseOf = Callable[['LLVMCodegen', ir.Value], ir.Value]


def _size_and_base(codegen: 'LLVMCodegen',
                   array_slot: ir.Value) -> tuple[ir.Value, _BaseOf, str]:
    """The length of an array slot, the reader of its first-element pointer, and the
    check prefix. The caller reads the base after the check.

    An indexable slot holds a FIXED array or a DYNAMIC array's anonymous {i32, i32, T*}
    descriptor, and nothing else. A user struct is an identified type since #257, so it
    cannot match the literal arm by shape.
    """
    from sushi_lang.backend import gep_utils

    array_type = array_slot.type.pointee
    match array_type:
        case ir.ArrayType():
            size_value: ir.Value = ir.Constant(codegen.i32, array_type.count)
            return size_value, _fixed_array_base, "array"
        case ir.LiteralStructType():
            len_ptr = gep_utils.gep_dynamic_array_len(codegen, array_slot, "len_ptr")
            size_value = codegen.builder.load(len_ptr, name="array_len")
            return size_value, _dynamic_array_base, "dynarray"
    raise_internal_error("CE0022", type=str(array_type))


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
