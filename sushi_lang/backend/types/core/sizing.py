"""Type size and alignment calculation for Sushi semantic types."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable

from sushi_lang.semantics.typesys import (
    Type as Ty, BuiltinType, ArrayType, DynamicArrayType, StructType,
    EnumType, UnknownType, IteratorType, ReferenceType, PointerType,
    ForeignPtrType, FunctionType
)
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.constants import FAT_POINTER_SIZE_BYTES, DYNAMIC_ARRAY_SIZE_BYTES, ITERATOR_SIZE_BYTES, ENUM_TAG_SIZE_BYTES
from sushi_lang.backend.types.core.resolution import resolve_unknown_type, resolve_generic_type_ref


def align_up(offset: int, alignment: int) -> int:
    """Round `offset` up to the next multiple of `alignment`."""
    return (offset + alignment - 1) // alignment * alignment


class TypeSizing:
    """Calculate sizes and alignments for Sushi types."""

    def __init__(self, struct_table: 'StructTable', enum_table: 'EnumTable'):
        """Initialize the type sizing calculator."""
        self.struct_table = struct_table
        self.enum_table = enum_table

    def get_type_size_bytes(self, semantic_type: Ty) -> int:
        """Get the size in bytes of a Sushi semantic type."""
        if isinstance(semantic_type, UnknownType):
            semantic_type = resolve_unknown_type(
                semantic_type, self.struct_table.by_name, self.enum_table.by_name
            )

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
                    return FAT_POINTER_SIZE_BYTES  # Fat pointer {i8*, i32, i8 owned} aligned sizeof = 16 (#145)
                case BuiltinType.STDIN | BuiltinType.STDOUT | BuiltinType.STDERR | BuiltinType.FILE:
                    return 8  # Pointer size (64-bit)
                case _:
                    raise_internal_error("CE0021", type=str(semantic_type))

        match semantic_type:
            case DynamicArrayType():
                return DYNAMIC_ARRAY_SIZE_BYTES
            case StructType():
                return self._calculate_struct_size(semantic_type)
            case ArrayType():
                element_size = self.get_type_size_bytes(semantic_type.base_type)
                return element_size * semantic_type.size
            case EnumType():
                # Enum: {i32 tag, [K x i64] data} (#300 phase 2). The payload starts at
                # offset 8 (the i64 array member gives the struct 8-alignment) and is a
                # whole number of i64 words, so the total is 8 + 8*K -- exactly LLVM's
                # sizeof for the mapped type.
                return ENUM_TAG_SIZE_BYTES + 8 * self.enum_payload_word_count(semantic_type)
            case IteratorType():
                return ITERATOR_SIZE_BYTES
            case ReferenceType():
                return 8  # 64-bit pointer
            case PointerType():
                return 8
            case ForeignPtrType():
                return 8
            case FunctionType():
                from sushi_lang.backend.constants.sizes import CLOSURE_FAT_POINTER_SIZE_BYTES
                return CLOSURE_FAT_POINTER_SIZE_BYTES
            case _:
                resolved = resolve_generic_type_ref(
                    semantic_type, self.struct_table.by_name, self.enum_table.by_name
                )
                if resolved is not None:
                    return self.get_type_size_bytes(resolved)
                raise_internal_error("CE0021", type=str(semantic_type))

    def _calculate_struct_size(self, struct_type: StructType) -> int:
        """Calculate total size of struct accounting for padding and alignment."""
        offset = 0
        max_align = 1  # Track maximum alignment requirement of all fields

        for _field_name, field_type in struct_type.fields:
            field_size = self.get_type_size_bytes(field_type)
            field_align = self.get_type_alignment(field_type)

            max_align = max(max_align, field_align)

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

        if offset % max_align != 0:
            padding = max_align - (offset % max_align)
            offset += padding

        return offset

    def payload_field_offsets(self, associated_types) -> list[int]:
        """The naturally aligned offset of each payload field, relative to the payload base."""
        offsets = []
        offset = 0
        for field_type in associated_types:
            offset = align_up(offset, self.get_type_alignment(field_type))
            offsets.append(offset)
            offset += self.get_type_size_bytes(field_type)
        return offsets

    def variant_payload_size(self, associated_types) -> int:
        """One variant's payload size in bytes, under the aligned layout."""
        if not associated_types:
            return 0
        offsets = self.payload_field_offsets(associated_types)
        return offsets[-1] + self.get_type_size_bytes(associated_types[-1])

    def enum_payload_word_count(self, enum_type: 'EnumType') -> int:
        """K in the enum's LLVM shape `{i32 tag, [K x i64] data}`: the widest variant's payload, in
        i64 words, minimum 1 (a payload-less enum keeps a 1-word array so the shape is uniform).
        """
        max_size = max(
            (self.variant_payload_size(v.associated_types)
             for v in enum_type.variants if v.associated_types),
            default=0,
        )
        return max(align_up(max_size, 8) // 8, 1)

    def get_type_alignment(self, semantic_type: Ty) -> int:
        """Get the alignment requirement in bytes for a semantic type."""
        if isinstance(semantic_type, UnknownType):
            if semantic_type.name in self.struct_table.by_name:
                semantic_type = self.struct_table.by_name[semantic_type.name]
            elif semantic_type.name in self.enum_table.by_name:
                semantic_type = self.enum_table.by_name[semantic_type.name]

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
                    return 8
                case BuiltinType.STDIN | BuiltinType.STDOUT | BuiltinType.STDERR | BuiltinType.FILE:
                    return 8  # Pointer alignment
                case _:
                    return 8  # Default to pointer alignment for unknown types

        match semantic_type:
            case DynamicArrayType():
                return 8
            case StructType():
                max_align = 1
                for _field_name, field_type in semantic_type.fields:
                    field_align = self.get_type_alignment(field_type)
                    max_align = max(max_align, field_align)
                return max_align
            case ArrayType():
                return self.get_type_alignment(semantic_type.base_type)
            case EnumType():
                # Enum aligned to its [K x i64] data member (#300 phase 2). Leaving this
                # at 4 would make the compiler's struct sizing disagree with LLVM's
                # stride for any struct holding an enum field.
                return 8
            case ReferenceType() | PointerType() | ForeignPtrType() | FunctionType():
                return 8
            case _:
                return 8
