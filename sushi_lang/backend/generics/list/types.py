"""LLVM type helpers for List<T>."""

from typing import Any
from sushi_lang.semantics.typesys import Type, StructType
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.constants.llvm_values import LIST_LEN_INDICES, LIST_CAP_INDICES, LIST_DATA_INDICES


def extract_element_type(list_type: StructType, codegen: Any) -> Type:
    """Extract T from List<T>."""
    name = list_type.name

    if not name.startswith("List<") or not name.endswith(">"):
        raise_internal_error("CE0049", generic="List", name=name)

    type_str = name[5:-1].strip()  # Remove "List<" and ">"

    from sushi_lang.semantics.generics.type_strings import resolve_type_from_string
    return resolve_type_from_string(type_str, codegen)


def get_list_llvm_type(codegen: Any, element_type: Type) -> ir.Type:
    """Get LLVM struct type for List<T>."""
    element_llvm = codegen.types.ll_type(element_type)
    len_llvm = codegen.types.i32
    capacity_llvm = codegen.types.i32
    data_llvm = ir.PointerType(element_llvm)

    return ir.LiteralStructType([len_llvm, capacity_llvm, data_llvm])


def get_list_element_type(codegen: Any, list_type: ir.Type) -> ir.Type:
    """Extract the element type from a List<T> LLVM type."""
    data_ptr_type = list_type.elements[2]
    return data_ptr_type.pointee


def get_list_len_ptr(builder: Any, list_value: ir.Value) -> ir.Value:
    """Get pointer to the len field of a List<T>."""
    return builder.gep(
        list_value,
        LIST_LEN_INDICES,
        name="list_len_ptr"
    )


def get_list_capacity_ptr(builder: Any, list_value: ir.Value) -> ir.Value:
    """Get pointer to the capacity field of a List<T>."""
    return builder.gep(
        list_value,
        LIST_CAP_INDICES,
        name="list_capacity_ptr"
    )


def get_list_data_ptr(builder: Any, list_value: ir.Value) -> ir.Value:
    """Get pointer to the data field of a List<T>."""
    return builder.gep(
        list_value,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )
