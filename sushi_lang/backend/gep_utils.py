"""
Centralized GEP (GetElementPtr) utilities for the Sushi language compiler.

This module provides type-safe, reusable helpers for all GEP operations,
eliminating manual index construction and reducing boilerplate across the codebase.

GEP Operations Reference:
-------------------------
1. Struct field access: gep(struct_ptr, [0, field_idx])
2. Array element access: gep(array_ptr, [index]) or gep(array_ptr, [0, index])
3. Dynamic array fields: gep(dynarray_ptr, [0, 0/1/2]) for len/cap/data
4. Byte offset access: gep(ptr, [offset]) for raw byte manipulation

Architecture:
-------------
This module consolidates GEP logic that was previously scattered across:
- backend/llvm_types.py (dynamic array field access)
- backend/statements/utils.py (struct field access)
- 25+ files with manual GEP construction

Benefits:
---------
- DRY compliance: Single source of truth for GEP patterns
- Type safety: Consistent index type handling (i32)
- Maintainability: Easy to update GEP logic in one place
- Readability: Self-documenting function names
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# ============================================================================
# Struct Field Access
# ============================================================================

def gep_struct_field(
    codegen: 'LLVMCodegen',
    struct_ptr: 'ir.Value',
    field_index: int,
    name: str = ""
) -> 'ir.Value':
    """Create a GEP instruction to access a struct field.

    This is the standard pattern for accessing struct fields: gep(ptr, [0, field_idx]).
    The first index (0) dereferences the pointer, the second selects the field.

    Args:
        codegen: The main LLVMCodegen instance.
        struct_ptr: The pointer to the struct.
        field_index: The index of the field to access (0-based).
        name: Optional name for the GEP instruction.

    Returns:
        Pointer to the struct field.

    Example:
        # Before (3 lines):
        zero = ir.Constant(codegen.types.i32, 0)
        field_idx = ir.Constant(codegen.types.i32, 2)
        ptr = codegen.builder.gep(struct_ptr, [zero, field_idx])

        # After (1 line):
        ptr = gep_struct_field(codegen, struct_ptr, 2, "field_ptr")
    """
    from llvmlite import ir
    zero = ir.Constant(codegen.types.i32, 0)
    field_idx_const = ir.Constant(codegen.types.i32, field_index)
    return codegen.builder.gep(struct_ptr, [zero, field_idx_const], name=name)


# ============================================================================
# Array Element Access
# ============================================================================

def gep_array_element(
    codegen: 'LLVMCodegen',
    array_ptr: 'ir.Value',
    index: 'ir.Value',
    name: str = ""
) -> 'ir.Value':
    """Create a GEP instruction to access an array element.

    This is for accessing elements in a flat array: gep(ptr, [index]).
    Used for dynamic array data access, fixed arrays, and contiguous memory.

    Args:
        codegen: The main LLVMCodegen instance.
        array_ptr: Pointer to the array data (T*).
        index: The index value (ir.Value of type i32).
        name: Optional name for the GEP instruction.

    Returns:
        Pointer to the array element.

    Example:
        # Before:
        element_ptr = codegen.builder.gep(data_ptr, [index_value])

        # After:
        element_ptr = gep_array_element(codegen, data_ptr, index_value, "elem_ptr")
    """
    return codegen.builder.gep(array_ptr, [index], name=name)


def gep_fixed_array_element(
    codegen: 'LLVMCodegen',
    array_ptr: 'ir.Value',
    index: 'ir.Value',
    name: str = ""
) -> 'ir.Value':
    """Create a GEP instruction to access a fixed array element.

    This is for accessing elements in a fixed-size array allocated on the stack
    or embedded in a struct: gep(ptr, [0, index]).

    Args:
        codegen: The main LLVMCodegen instance.
        array_ptr: Pointer to the fixed array.
        index: The index value (ir.Value of type i32).
        name: Optional name for the GEP instruction.

    Returns:
        Pointer to the array element.

    Example:
        # Before:
        zero = ir.Constant(codegen.types.i32, 0)
        element_ptr = codegen.builder.gep(array_ptr, [zero, index])

        # After:
        element_ptr = gep_fixed_array_element(codegen, array_ptr, index, "elem_ptr")
    """
    from llvmlite import ir
    zero = ir.Constant(codegen.types.i32, 0)
    return codegen.builder.gep(array_ptr, [zero, index], name=name)


# ============================================================================
# Dynamic Array Field Access
# ============================================================================

def gep_dynamic_array_len(
    codegen: 'LLVMCodegen',
    array_struct_ptr: 'ir.Value',
    name: str = "len_ptr"
) -> 'ir.Value':
    """Get pointer to the 'len' field of a dynamic array struct.

    Dynamic array struct layout: {i32 len, i32 cap, T* data}
    This accesses field index 0 (len).

    Args:
        codegen: The main LLVMCodegen instance.
        array_struct_ptr: Pointer to the dynamic array struct.
        name: Optional name for the GEP instruction (default: "len_ptr").

    Returns:
        Pointer to the len field (i32*).

    Example:
        # Before:
        len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_ptr)

        # After:
        len_ptr = gep_dynamic_array_len(codegen, array_ptr)
    """
    return gep_struct_field(codegen, array_struct_ptr, 0, name)


def gep_dynamic_array_cap(
    codegen: 'LLVMCodegen',
    array_struct_ptr: 'ir.Value',
    name: str = "cap_ptr"
) -> 'ir.Value':
    """Get pointer to the 'cap' field of a dynamic array struct.

    Dynamic array struct layout: {i32 len, i32 cap, T* data}
    This accesses field index 1 (cap).

    Args:
        codegen: The main LLVMCodegen instance.
        array_struct_ptr: Pointer to the dynamic array struct.
        name: Optional name for the GEP instruction (default: "cap_ptr").

    Returns:
        Pointer to the cap field (i32*).

    Example:
        # Before:
        cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_ptr)

        # After:
        cap_ptr = gep_dynamic_array_cap(codegen, array_ptr)
    """
    return gep_struct_field(codegen, array_struct_ptr, 1, name)


def gep_dynamic_array_data(
    codegen: 'LLVMCodegen',
    array_struct_ptr: 'ir.Value',
    name: str = "data_ptr"
) -> 'ir.Value':
    """Get pointer to the 'data' field of a dynamic array struct.

    Dynamic array struct layout: {i32 len, i32 cap, T* data}
    This accesses field index 2 (data).

    Args:
        codegen: The main LLVMCodegen instance.
        array_struct_ptr: Pointer to the dynamic array struct.
        name: Optional name for the GEP instruction (default: "data_ptr").

    Returns:
        Pointer to the data field (T**).

    Example:
        # Before:
        data_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_ptr)

        # After:
        data_ptr = gep_dynamic_array_data(codegen, array_ptr)
    """
    return gep_struct_field(codegen, array_struct_ptr, 2, name)


# ============================================================================
# Byte Offset Access (for enums, raw memory)
# ============================================================================

def gep_byte_offset(
    codegen: 'LLVMCodegen',
    ptr: 'ir.Value',
    offset: 'ir.Value',
    name: str = ""
) -> 'ir.Value':
    """Create a GEP instruction for byte-level pointer arithmetic.

    This is used for raw memory access, enum variant data, and other
    cases where we need to offset a pointer by a number of bytes.

    Args:
        codegen: The main LLVMCodegen instance.
        ptr: The base pointer (typically i8*).
        offset: The byte offset (ir.Value of type i32).
        name: Optional name for the GEP instruction.

    Returns:
        Offset pointer.

    Example:
        # Before:
        offset_ptr = builder.gep(data_ptr, [ir.Constant(codegen.types.i32, 8)])

        # After:
        offset_const = ir.Constant(codegen.types.i32, 8)
        offset_ptr = gep_byte_offset(codegen, data_ptr, offset_const, "variant_ptr")
    """
    return codegen.builder.gep(ptr, [offset], name=name)


# ============================================================================
# Backward Compatibility Aliases
# ============================================================================

# These aliases maintain compatibility with existing code during migration.
# They delegate to the new centralized functions.

def get_dynamic_array_len_ptr(codegen: 'LLVMCodegen', array_ptr: 'ir.Value') -> 'ir.Value':
    """Backward compatibility alias for gep_dynamic_array_len."""
    return gep_dynamic_array_len(codegen, array_ptr)


def get_dynamic_array_cap_ptr(codegen: 'LLVMCodegen', array_ptr: 'ir.Value') -> 'ir.Value':
    """Backward compatibility alias for gep_dynamic_array_cap."""
    return gep_dynamic_array_cap(codegen, array_ptr)


def get_dynamic_array_data_ptr(codegen: 'LLVMCodegen', array_ptr: 'ir.Value') -> 'ir.Value':
    """Backward compatibility alias for gep_dynamic_array_data."""
    return gep_dynamic_array_data(codegen, array_ptr)
