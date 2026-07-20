"""
Helper utilities for dynamic array operations.

This module provides shared utility functions for creating and initializing
dynamic array structures, used by both constructors and struct field initialization.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_array_literal_elements(codegen: 'LLVMCodegen', element_exprs, element_type) -> list[ir.Value]:
    """Emit array-literal element values, deep-copying heap-owning aliases.

    A bare-``Name`` / member-access element aliases a live owner (a registered local or a
    struct field that stays live and is itself RAII-freed). Storing the shallow value into
    the array would make the array and the source share the same heap buffer, so both free
    it at scope exit (double-free). Deep-copy such aliases so each side owns independent
    buffers and frees exactly once -- mirrors ``_clone_owning_struct_alias`` for
    let-bindings (#60/#147). A fresh temp (constructor / call return) is the sole owner and
    is moved into the array unchanged (cloning it would orphan the original buffer).

    ``emit_value_clone`` is a no-op for a non-owning element type, so this only allocates
    when there is a heap buffer to duplicate.

    Args:
        codegen: The LLVM codegen instance.
        element_exprs: The element AST expressions.
        element_type: The semantic element type (used to drive the deep copy); when None,
            no cloning is attempted (values are emitted verbatim).

    Returns:
        The list of emitted (and alias-cloned) element SSA values.
    """
    from sushi_lang.semantics.ast import Name, MemberAccess
    from sushi_lang.backend.expressions.memory import emit_value_clone
    from sushi_lang.semantics.typesys import type_moves_by_value

    values = []
    for elem in element_exprs:
        value = codegen.expressions.emit_expr(elem)
        if isinstance(elem, (Name, MemberAccess)):
            ety = element_type if element_type is not None else _alias_element_type(codegen, elem)
            if ety is not None:
                # #134: a bare-Name element of a MOVE type moves into the array -- mark the
                # source moved and store it un-cloned. A MemberAccess element reads from a
                # continuing owner (V5) and keeps the deep copy.
                if isinstance(elem, Name) and type_moves_by_value(ety):
                    codegen.memory.mark_struct_as_moved(elem.id)
                else:
                    value = emit_value_clone(codegen, value, ety)
        values.append(value)
    return values


def _alias_element_type(codegen: 'LLVMCodegen', elem):
    """Best-effort semantic type of a bare-Name array-literal element (for alias cloning).

    Used by callers (e.g. the plain `from([...])` path) that do not know the declared
    element type. A bare local resolves through the scope's semantic-type table; anything
    else returns None (no clone attempted -- ``emit_value_clone`` is only skipped, never
    wrong, so a missed alias is a leak-free no-op here at worst).
    """
    from sushi_lang.semantics.ast import Name
    if isinstance(elem, Name):
        return codegen.memory.get_semantic_type(elem.id)
    return None


def create_dynamic_array_from_elements(codegen: 'LLVMCodegen', element_type, element_llvm_type: ir.Type,
                                       elements: list[ir.Value]) -> ir.Value:
    """Create a dynamic array struct value from a list of elements.

    Allocates memory with power-of-2 capacity and initializes with provided elements.
    Used by struct constructors and from() constructor.

    Args:
        codegen: The LLVM codegen instance.
        element_type: The Sushi language type of elements.
        element_llvm_type: The LLVM type of elements.
        elements: List of LLVM values for the array elements.

    Returns:
        An LLVM struct value representing the dynamic array.

    Note:
        May emit runtime error RE2021 if realloc fails.
    """
    from sushi_lang.backend.expressions import memory

    # Calculate capacity (next power of 2)
    initial_len = len(elements)
    if initial_len == 0:
        # Empty array
        zero_i32 = ir.Constant(codegen.types.i32, 0)
        null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
        array_struct_type = ir.LiteralStructType([
            codegen.types.i32,
            codegen.types.i32,
            ir.PointerType(element_llvm_type)
        ])
        array_struct = ir.Constant(array_struct_type, ir.Undefined)
        array_struct = codegen.builder.insert_value(array_struct, zero_i32, 0)
        array_struct = codegen.builder.insert_value(array_struct, zero_i32, 1)
        array_struct = codegen.builder.insert_value(array_struct, null_ptr, 2)
        return array_struct

    # Calculate capacity (power of 2)
    capacity = 1
    while capacity < initial_len:
        capacity *= 2

    # Allocate memory.
    # Use the LLVM ABI alloc size of the element type, which equals the stride
    # used by getelementptr on the element pointer. The semantic data size can be
    # smaller than the alloc size for padded types -- e.g. a string fat pointer
    # {i8*, i32} has a 12-byte data size but a 16-byte alloc size (8-byte aligned).
    # Allocating with the data size while GEP strides by the alloc size overflows
    # the buffer past element 0 and corrupts the heap (issues #24 / #29). The
    # grow, clone, and new() paths already size with this helper.
    element_size = memory.get_element_size_constant(codegen, element_llvm_type)
    capacity_val = ir.Constant(codegen.types.i32, capacity)
    total_bytes = codegen.builder.mul(capacity_val, element_size, name="total_bytes")

    # Allocate memory using realloc with null pointer (acts as malloc)
    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    data_ptr = memory.emit_realloc_call(codegen, null_ptr, total_bytes)

    # Cast void* to element_type*
    typed_data_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(element_llvm_type))

    # Copy elements to allocated memory
    for i, element_value in enumerate(elements):
        element_ptr = codegen.builder.gep(typed_data_ptr, [ir.Constant(codegen.types.i32, i)])
        codegen.builder.store(element_value, element_ptr)

    # Create the array struct
    array_struct_type = ir.LiteralStructType([
        codegen.types.i32,
        codegen.types.i32,
        ir.PointerType(element_llvm_type)
    ])

    len_val = ir.Constant(codegen.types.i32, initial_len)
    cap_val = ir.Constant(codegen.types.i32, capacity)

    array_struct = ir.Constant(array_struct_type, ir.Undefined)
    array_struct = codegen.builder.insert_value(array_struct, len_val, 0)
    array_struct = codegen.builder.insert_value(array_struct, cap_val, 1)
    array_struct = codegen.builder.insert_value(array_struct, typed_data_ptr, 2)

    return array_struct
