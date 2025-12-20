"""Type size and alignment calculation for Sushi semantic types.

This module provides utilities for calculating memory sizes and alignment
requirements for Sushi types when mapped to LLVM IR.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable

from sushi_lang.semantics.typesys import (
    Type as Ty, BuiltinType, ArrayType, DynamicArrayType, StructType,
    EnumType, UnknownType, ResultType, IteratorType, ReferenceType, PointerType
)
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.constants import FAT_POINTER_SIZE_BYTES, DYNAMIC_ARRAY_SIZE_BYTES, ITERATOR_SIZE_BYTES, ENUM_TAG_SIZE_BYTES
from sushi_lang.backend.types.core.resolution import resolve_unknown_type, resolve_generic_type_ref, calculate_max_variant_size


class TypeSizing:
    """Calculate sizes and alignments for Sushi types."""

    def __init__(self, struct_table: 'StructTable', enum_table: 'EnumTable'):
        """Initialize the type sizing calculator.

        Args:
            struct_table: Table for resolving struct types.
            enum_table: Table for resolving enum types.
        """
        self.struct_table = struct_table
        self.enum_table = enum_table

    def get_type_size_bytes(self, semantic_type: Ty) -> int:
        """Get the size in bytes of a Sushi semantic type.

        This is the single source of truth for type sizes in the compiler.
        Returns size as a Python int for creating LLVM constants.

        Args:
            semantic_type: The Sushi language type.

        Returns:
            Size in bytes as Python int.

        Raises:
            InternalError: If the semantic type is not supported.
        """
        # Resolve UnknownType first using shared helper
        if isinstance(semantic_type, UnknownType):
            semantic_type = resolve_unknown_type(
                semantic_type, self.struct_table.by_name, self.enum_table.by_name
            )

        # Builtin types
        if isinstance(semantic_type, BuiltinType):
            match semantic_type:
                case BuiltinType.I8 | BuiltinType.U8 | BuiltinType.BOOL:
                    return 1
                case BuiltinType.I16 | BuiltinType.U16:
                    return 2
                case BuiltinType.I32 | BuiltinType.U32 | BuiltinType.F32 | BuiltinType.BLANK:
                    return 4
                case BuiltinType.I64 | BuiltinType.U64 | BuiltinType.F64:
                    return 8
                case BuiltinType.STRING:
                    return FAT_POINTER_SIZE_BYTES  # Fat pointer: {i8* data, i32 size} = 8 + 4 = 12 bytes
                case BuiltinType.STDIN | BuiltinType.STDOUT | BuiltinType.STDERR | BuiltinType.FILE:
                    return 8  # Pointer size (64-bit)
                case _:
                    raise_internal_error("CE0021", type=str(semantic_type))

        # Complex types
        match semantic_type:
            case DynamicArrayType():
                # Dynamic array struct: {i32 len, i32 cap, T* data} = 4 + 4 + 8 = 16 bytes
                return DYNAMIC_ARRAY_SIZE_BYTES
            case StructType():
                # Recursive calculation for structs
                return self._calculate_struct_size(semantic_type)
            case ArrayType():
                # Fixed array: element_size * count
                element_size = self.get_type_size_bytes(semantic_type.base_type)
                return element_size * semantic_type.size
            case EnumType():
                # Enum: {i32 tag, [max_size x i8] data}
                max_variant_size = calculate_max_variant_size(
                    semantic_type, self.get_type_size_bytes
                )
                # Tag (4 bytes) + data array (max_variant_size, minimum 1)
                return ENUM_TAG_SIZE_BYTES + max(max_variant_size, 1)
            case IteratorType():
                # Iterator struct: {i32 current_index, i32 length, T* data_ptr} = 4 + 4 + 8 = 16 bytes
                return ITERATOR_SIZE_BYTES
            case ReferenceType():
                # References compile to pointers
                return 8  # 64-bit pointer
            case PointerType():
                # Pointers are always 8 bytes (64-bit)
                return 8
            case ResultType():
                # Result<T, E> - ensure the corresponding enum exists and calculate its size
                from sushi_lang.backend.generics.results import ensure_result_type_in_table
                result_enum = ensure_result_type_in_table(
                    self.enum_table,
                    semantic_type.ok_type,
                    semantic_type.err_type
                )
                return self.get_type_size_bytes(result_enum)
            case _:
                # Check if this is a GenericTypeRef using shared helper
                resolved = resolve_generic_type_ref(
                    semantic_type, self.struct_table.by_name, self.enum_table.by_name
                )
                if resolved is not None:
                    return self.get_type_size_bytes(resolved)
                raise_internal_error("CE0021", type=str(semantic_type))

    def _calculate_struct_size(self, struct_type: StructType) -> int:
        """Calculate total size of struct accounting for padding and alignment.

        This function properly calculates struct sizes including padding needed
        for field alignment, matching LLVM's struct layout rules for x86-64 ABI.

        Args:
            struct_type: The struct type to calculate size for.

        Returns:
            Total size in bytes including padding.
        """
        offset = 0
        max_align = 1  # Track maximum alignment requirement of all fields

        for field_name, field_type in struct_type.fields:
            # Get the size and alignment requirements for this field
            field_size = self.get_type_size_bytes(field_type)
            field_align = self.get_type_alignment(field_type)

            # Track maximum alignment
            max_align = max(max_align, field_align)

            # Add padding to align this field
            if offset % field_align != 0:
                padding = field_align - (offset % field_align)
                offset += padding

            # Add the field size, rounded up to its alignment
            # This accounts for tail padding that LLVM adds to nested structs
            # For example, {i8*, i32} has size 12 but takes 16 bytes when embedded
            if field_size % field_align != 0:
                field_size_with_padding = field_size + (field_align - (field_size % field_align))
                offset += field_size_with_padding
            else:
                offset += field_size

        # Add final padding to align the entire struct
        if offset % max_align != 0:
            padding = max_align - (offset % max_align)
            offset += padding

        return offset

    def get_type_alignment(self, semantic_type: Ty) -> int:
        """Get the alignment requirement in bytes for a semantic type.

        Alignment rules for x86-64:
        - i8/u8/bool: 1 byte
        - i16/u16: 2 bytes
        - i32/u32/f32: 4 bytes
        - i64/u64/f64/pointers: 8 bytes
        - Structs: maximum alignment of all fields
        - Arrays: alignment of element type

        Args:
            semantic_type: The Sushi language type.

        Returns:
            Alignment in bytes.
        """
        # Resolve UnknownType first
        if isinstance(semantic_type, UnknownType):
            if semantic_type.name in self.struct_table.by_name:
                semantic_type = self.struct_table.by_name[semantic_type.name]
            elif semantic_type.name in self.enum_table.by_name:
                semantic_type = self.enum_table.by_name[semantic_type.name]

        # Builtin types
        if isinstance(semantic_type, BuiltinType):
            match semantic_type:
                case BuiltinType.I8 | BuiltinType.U8 | BuiltinType.BOOL:
                    return 1
                case BuiltinType.I16 | BuiltinType.U16:
                    return 2
                case BuiltinType.I32 | BuiltinType.U32 | BuiltinType.F32 | BuiltinType.BLANK:
                    return 4
                case BuiltinType.I64 | BuiltinType.U64 | BuiltinType.F64:
                    return 8
                case BuiltinType.STRING:
                    # String fat pointer struct: {i8*, i32} aligned to 8 bytes (pointer alignment)
                    return 8
                case BuiltinType.STDIN | BuiltinType.STDOUT | BuiltinType.STDERR | BuiltinType.FILE:
                    return 8  # Pointer alignment
                case _:
                    return 8  # Default to pointer alignment for unknown types

        # Complex types
        match semantic_type:
            case DynamicArrayType():
                # Dynamic array struct aligned to 8 bytes (pointer alignment)
                return 8
            case StructType():
                # Struct aligned to maximum alignment of its fields
                max_align = 1
                for field_name, field_type in semantic_type.fields:
                    field_align = self.get_type_alignment(field_type)
                    max_align = max(max_align, field_align)
                return max_align
            case ArrayType():
                # Array aligned to element alignment
                return self.get_type_alignment(semantic_type.base_type)
            case EnumType():
                # Enum aligned to i32 tag (4 bytes)
                return 4
            case ReferenceType() | PointerType():
                # Pointers aligned to 8 bytes
                return 8
            case _:
                # Default to 8 bytes for unknown types (pointer alignment)
                return 8
