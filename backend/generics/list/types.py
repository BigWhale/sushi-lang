"""
LLVM type helpers for List<T>.

This module provides functions to create LLVM struct types for List<T>.
"""

from typing import Any
from semantics.typesys import Type, StructType
import llvmlite.ir as ir
from internals.errors import raise_internal_error
from backend.llvm_constants import LIST_LEN_INDICES, LIST_CAP_INDICES, LIST_DATA_INDICES


def extract_element_type(list_type: StructType, codegen: Any) -> Type:
    """Extract T from List<T>.

    Parses the struct name "List<T>" to extract the concrete element type.
    This works because monomorphized generic structs have names like "List<i32>".

    Args:
        list_type: The List<T> struct type (after monomorphization).
        codegen: LLVM codegen instance.

    Returns:
        The element type T.

    Raises:
        ValueError: If the type name cannot be parsed or type T is not found.
    """
    name = list_type.name

    # Expected format: "List<T>"
    if not name.startswith("List<") or not name.endswith(">"):
        raise_internal_error("CE0049", generic="List", name=name)

    # Extract T from "List<T>"
    type_str = name[5:-1].strip()  # Remove "List<" and ">"

    # Parse the type string (might be builtin, struct, enum, or nested generic)
    from backend.generics.hashmap.types import resolve_type_from_string
    return resolve_type_from_string(type_str, codegen)


def get_list_llvm_type(codegen: Any, element_type: Type) -> ir.Type:
    """Get LLVM struct type for List<T>.

    Structure:
        struct List<T>:
            i32 len       # Current number of elements
            i32 capacity  # Allocated capacity
            T* data       # Pointer to heap-allocated array

    Args:
        codegen: LLVM codegen instance.
        element_type: The element type T.

    Returns:
        LLVM literal struct type for List<T>.
    """
    element_llvm = codegen.types.ll_type(element_type)
    len_llvm = codegen.types.i32
    capacity_llvm = codegen.types.i32
    data_llvm = ir.PointerType(element_llvm)

    return ir.LiteralStructType([len_llvm, capacity_llvm, data_llvm])


def get_list_element_type(codegen: Any, list_type: ir.Type) -> ir.Type:
    """Extract the element type from a List<T> LLVM type.

    Args:
        codegen: LLVM codegen instance.
        list_type: The List<T> LLVM struct type.

    Returns:
        The element type T as LLVM type.
    """
    # data field is at index 2, it's a pointer type
    data_ptr_type = list_type.elements[2]
    return data_ptr_type.pointee


def get_list_len_ptr(builder: Any, list_value: ir.Value) -> ir.Value:
    """Get pointer to the len field of a List<T>.

    Args:
        builder: LLVM IR builder.
        list_value: The List<T> struct value.

    Returns:
        Pointer to the len field (i32*).
    """
    return builder.gep(
        list_value,
        LIST_LEN_INDICES,
        name="list_len_ptr"
    )


def get_list_capacity_ptr(builder: Any, list_value: ir.Value) -> ir.Value:
    """Get pointer to the capacity field of a List<T>.

    Args:
        builder: LLVM IR builder.
        list_value: The List<T> struct value.

    Returns:
        Pointer to the capacity field (i32*).
    """
    return builder.gep(
        list_value,
        LIST_CAP_INDICES,
        name="list_capacity_ptr"
    )


def get_list_data_ptr(builder: Any, list_value: ir.Value) -> ir.Value:
    """Get pointer to the data field of a List<T>.

    Args:
        builder: LLVM IR builder.
        list_value: The List<T> struct value.

    Returns:
        Pointer to the data field (T**).
    """
    return builder.gep(
        list_value,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )
