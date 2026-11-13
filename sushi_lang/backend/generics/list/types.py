"""LLVM type helpers for List<T>."""

from typing import Any
from sushi_lang.semantics.typesys import Type, StructType
import llvmlite.ir as ir
from sushi_lang.backend import gep_utils
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.constants.llvm_values import LIST_DATA_INDICES


def extract_element_type(list_type: StructType, codegen: Any) -> Type:
    """Extract T from List<T>."""
    from sushi_lang.semantics.generics.list import parse_list_types

    element = parse_list_types(list_type, codegen)
    if element is None:
        raise_internal_error("CE0049", generic="List", name=list_type.name)
    return element


def get_list_llvm_type(codegen: Any, element_type: Type) -> ir.Type:
    """The LLVM type of a List<T>: the `T[]` descriptor `{i64 len, i64 cap, T* data}`."""
    from sushi_lang.sushi_stdlib.src.type_definitions import get_dynamic_array_type
    return get_dynamic_array_type(codegen.types.ll_type(element_type))


def get_list_element_type(codegen: Any, list_type: ir.Type) -> ir.Type:
    """Extract the element type from a List<T> LLVM type."""
    data_ptr_type = list_type.elements[2]
    return data_ptr_type.pointee


def get_list_data_ptr(builder: Any, list_value: ir.Value) -> ir.Value:
    """Get pointer to the data field of a List<T>."""
    return builder.gep(
        list_value,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )


def list_slots(codegen: Any, list_ptr: ir.Value) -> tuple[ir.Value, ir.Value]:
    """The length and the data of a List at an address."""
    b = codegen.builder
    length = gep_utils.load_len(codegen, list_ptr, "list_len", builder=b)
    data = b.load(get_list_data_ptr(b, list_ptr), name="list_data")
    return length, data
