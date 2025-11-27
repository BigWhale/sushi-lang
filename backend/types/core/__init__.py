"""Unified LLVM type system facade for the Sushi compiler.

This module provides a clean interface to the type system components,
hiding the internal organization and providing a single entry point.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semantics.passes.collect import StructTable, EnumTable
    from semantics.typesys import Type as Ty
    from llvmlite import ir

from backend.types.core.caching import TypeCache
from backend.types.core.sizing import TypeSizing
from backend.types.core.mapping import TypeMapper
from backend.types.core.inference import TypeInference


class LLVMTypeSystem:
    """Unified interface to LLVM type system.

    This facade provides access to all type system operations:
    - Type mapping (Sushi -> LLVM)
    - Type inference (LLVM -> Sushi)
    - Type sizing and alignment
    - Struct/enum type caching
    """

    def __init__(
        self,
        struct_table: StructTable | None = None,
        enum_table: EnumTable | None = None,
    ):
        """Initialize the unified type system.

        Args:
            struct_table: Optional struct table for resolving struct types
            enum_table: Optional enum table for resolving enum types
        """
        from semantics.passes.collect import StructTable, EnumTable

        self.struct_table = struct_table or StructTable()
        self.enum_table = enum_table or EnumTable()

        # Initialize subsystems
        self.cache = TypeCache()
        self.sizing = TypeSizing(self.struct_table, self.enum_table)
        self.mapper = TypeMapper(self.cache, self.struct_table, self.enum_table)
        self.inference = TypeInference(
            self.mapper.i8,
            self.mapper.i32,
            self.mapper.string_struct,
        )

        # Expose LLVM primitive types from mapper for direct access
        self.i1 = self.mapper.i1
        self.i8 = self.mapper.i8
        self.i16 = self.mapper.i16
        self.i32 = self.mapper.i32
        self.i64 = self.mapper.i64
        self.u8 = self.mapper.u8
        self.u16 = self.mapper.u16
        self.u32 = self.mapper.u32
        self.u64 = self.mapper.u64
        self.f32 = self.mapper.f32
        self.f64 = self.mapper.f64
        self.str_ptr = self.mapper.str_ptr
        self.void = self.mapper.void
        self.string_struct = self.mapper.string_struct

    # Type mapping interface
    def ll_type(self, semantic_type: Ty) -> ir.Type:
        """Convert Sushi type to LLVM type.

        Args:
            semantic_type: The Sushi language type

        Returns:
            Corresponding LLVM IR type
        """
        return self.mapper.ll_type(semantic_type)

    # Type inference interface
    def infer_llvm_type_from_value(self, value: ir.Value) -> ir.Type:
        """Infer LLVM type from runtime value.

        Args:
            value: LLVM value to analyze

        Returns:
            LLVM type of the value
        """
        return self.inference.infer_llvm_type_from_value(value)

    def map_llvm_to_language_type(self, llvm_type: ir.Type) -> str:
        """Map LLVM type back to language type name.

        Args:
            llvm_type: LLVM IR type

        Returns:
            Sushi language type name
        """
        return self.inference.map_llvm_to_language_type(llvm_type)

    # Type checking utilities
    def is_string_type(self, llvm_type: ir.Type) -> bool:
        """Check if LLVM type represents a string."""
        return self.inference.is_string_type(llvm_type)

    def is_integer_type(self, llvm_type: ir.Type, width: int | None = None) -> bool:
        """Check if LLVM type is an integer with optional width constraint."""
        return self.inference.is_integer_type(llvm_type, width)

    def is_dynamic_array_type(self, llvm_type: ir.Type) -> bool:
        """Check if LLVM type represents a dynamic array."""
        return self.inference.is_dynamic_array_type(llvm_type)

    # Type sizing interface
    def get_type_size_bytes(self, semantic_type: Ty) -> int:
        """Get size in bytes of a Sushi type.

        Args:
            semantic_type: The Sushi language type

        Returns:
            Size in bytes
        """
        return self.sizing.get_type_size_bytes(semantic_type)

    def get_type_size_constant(self, semantic_type: Ty) -> ir.Value:
        """Get size in bytes as an LLVM i32 constant.

        Args:
            semantic_type: The Sushi language type

        Returns:
            LLVM i32 constant with size in bytes
        """
        from llvmlite import ir
        size_bytes = self.sizing.get_type_size_bytes(semantic_type)
        return ir.Constant(self.i32, size_bytes)

    def _get_type_alignment(self, semantic_type: Ty) -> int:
        """Get alignment requirement for a Sushi type.

        Args:
            semantic_type: The Sushi language type

        Returns:
            Alignment in bytes
        """
        return self.sizing.get_type_alignment(semantic_type)

    # Struct/enum type helpers (delegated to mapper)
    def get_string_struct_type(self) -> ir.LiteralStructType:
        """Get LLVM struct type for strings: {i8* data, i32 size}"""
        return self.mapper.string_struct

    def get_dynamic_array_struct_type(self, element_type: ir.Type) -> ir.LiteralStructType:
        """Get LLVM struct type for dynamic arrays: {i32 len, i32 cap, T* data}"""
        return self.mapper._create_dynamic_array_struct_type(element_type)

    def get_struct_type(self, struct_type) -> ir.LiteralStructType:
        """Get LLVM struct type for user-defined structs."""
        return self.mapper._get_struct_type(struct_type)

    def get_enum_type(self, enum_type) -> ir.LiteralStructType:
        """Get LLVM struct type for enums (tagged unions)."""
        return self.mapper._get_enum_type(enum_type)

    def get_iterator_struct_type(self, iterator_type) -> ir.LiteralStructType:
        """Get LLVM struct type for Iterator<T>."""
        return self.mapper._create_iterator_struct_type(iterator_type)

    # GEP helpers for dynamic arrays
    def get_dynamic_array_len_ptr(self, builder: ir.IRBuilder, array_ptr: ir.Value) -> ir.Value:
        """Get pointer to 'len' field of dynamic array struct."""
        from llvmlite import ir
        zero = ir.Constant(self.i32, 0)
        field_idx = ir.Constant(self.i32, 0)
        return builder.gep(array_ptr, [zero, field_idx], name="len_ptr")

    def get_dynamic_array_cap_ptr(self, builder: ir.IRBuilder, array_ptr: ir.Value) -> ir.Value:
        """Get pointer to 'cap' field of dynamic array struct."""
        from llvmlite import ir
        zero = ir.Constant(self.i32, 0)
        field_idx = ir.Constant(self.i32, 1)
        return builder.gep(array_ptr, [zero, field_idx], name="cap_ptr")

    def get_dynamic_array_data_ptr(self, builder: ir.IRBuilder, array_ptr: ir.Value) -> ir.Value:
        """Get pointer to 'data' field of dynamic array struct."""
        from llvmlite import ir
        zero = ir.Constant(self.i32, 0)
        field_idx = ir.Constant(self.i32, 2)
        return builder.gep(array_ptr, [zero, field_idx], name="data_ptr")

    def _calculate_struct_size(self, struct_type) -> int:
        """Calculate total size of struct accounting for padding and alignment."""
        return self.sizing._calculate_struct_size(struct_type)
