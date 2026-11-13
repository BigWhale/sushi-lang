"""The element address, the length and the slice view of an array or a string (docs/design/ir.md 8.4, 8.7).

Each function takes the ADDRESS of the storage of a `T[N]`, a `T[]` or a `string` and its
semantic type, never a layout match. SLIR lowering placed the bounds check before each
access, so nothing here checks. A `T[]` descriptor field is read through the `gep_utils`
trio, the one descriptor GEP seam.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.list.types import get_list_data_ptr, get_list_len_ptr
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import ArrayType, BuiltinType, DynamicArrayType, Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

_STRING_DATA, _STRING_SIZE = 0, 1


def element_address(codegen: 'LLVMCodegen', base: ir.Value, ty: Type,
                    index: ir.Value) -> ir.Value:
    """The address of element `index` of the array, the `List@(T)` or the string at `base`."""
    if isinstance(ty, ArrayType):
        return gep_utils.gep_fixed_array_element(codegen, base, index, "elem_ptr")
    if is_instance_of(ty, "List"):
        data = codegen.builder.load(get_list_data_ptr(codegen.builder, base), name="list_data")
        return gep_utils.gep_array_element(codegen, data, index, "elem_ptr")
    return gep_utils.gep_array_element(codegen, array_data(codegen, base, ty), index, "elem_ptr")


def array_length(codegen: 'LLVMCodegen', base: ir.Value, ty: Type) -> ir.Value:
    """The length of the array, the `List@(T)` or the string at `base`, as an `i32`."""
    if isinstance(ty, ArrayType):
        return ir.Constant(codegen.types.i32, ty.size)
    if isinstance(ty, DynamicArrayType):
        return codegen.builder.load(gep_utils.gep_dynamic_array_len(codegen, base),
                                    name="array_len")
    if ty == BuiltinType.STRING:
        return codegen.builder.load(gep_utils.gep_struct_field(codegen, base, _STRING_SIZE),
                                    name="string_len")
    if is_instance_of(ty, "List"):
        return codegen.builder.load(get_list_len_ptr(codegen.builder, base), name="list_len")
    raise_internal_error("CE0000", detail=f"the length of {ty}, which is no array")


def slice_value(codegen: 'LLVMCodegen', base: ir.Value, ty: Type, start: ir.Value,
                end: ir.Value) -> ir.Value:
    """`data[start..end]`: the descriptor `{end - start, end - start, data + start}`, by
    value. It owns nothing: the array keeps its storage (#1226). The bounds are i64, and the
    check before the slice proved `0 <= start <= end <= len`, so the length fits the i32
    of the descriptor."""
    from sushi_lang.backend.types.arrays.utils import descriptor

    if not isinstance(ty, (ArrayType, DynamicArrayType)):
        raise_internal_error("CE0000", detail=f"a slice of {ty}, which is no array")
    data = gep_utils.gep_array_element(codegen, array_data(codegen, base, ty), start, "slice_data")
    length = codegen.builder.trunc(codegen.builder.sub(end, start, name="slice_len"),
                                   codegen.types.i32, name="slice_len_i32")
    return descriptor(codegen, codegen.types.ll_type(ty.base_type), length, length, data)


def array_data(codegen: 'LLVMCodegen', base: ir.Value, ty: Type) -> ir.Value:
    """The address of the first element of the array or the string at `base`."""
    if isinstance(ty, ArrayType):
        zero = ir.Constant(codegen.types.i32, 0)
        return codegen.builder.gep(base, [zero, zero], name="first_elem")
    if isinstance(ty, DynamicArrayType):
        return codegen.builder.load(gep_utils.gep_dynamic_array_data(codegen, base),
                                    name="array_data")
    if ty == BuiltinType.STRING:
        return codegen.builder.load(gep_utils.gep_struct_field(codegen, base, _STRING_DATA),
                                    name="string_data")
    raise_internal_error("CE0000", detail=f"an element of {ty}, which is no array")
