"""Type mapping from Sushi semantic types to LLVM IR types.

This module handles the conversion from Sushi language types to their
LLVM IR representations used in code generation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semantics.passes.collect import StructTable, EnumTable
    from backend.types.core.caching import TypeCache

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from semantics.typesys import (
    Type as Ty,
    BuiltinType,
    ArrayType,
    DynamicArrayType,
    StructType,
    EnumType,
    UnknownType,
    ResultType,
    IteratorType,
    ReferenceType,
    PointerType,
)
from internals.errors import raise_internal_error
from backend.types.core.resolution import resolve_unknown_type, resolve_generic_type_ref, calculate_max_variant_size


class TypeMapper:
    """Maps Sushi semantic types to LLVM IR types."""

    def __init__(
        self,
        cache: TypeCache,
        struct_table: StructTable,
        enum_table: EnumTable,
    ):
        """Initialize type mapper with caching and type tables.

        Args:
            cache: TypeCache for struct/enum caching
            struct_table: Struct table for resolving struct types
            enum_table: Enum table for resolving enum types
        """
        self.cache = cache
        self.struct_table = struct_table
        self.enum_table = enum_table

        # LLVM primitive types
        self.i8: ir.IntType = ir.IntType(INT8_BIT_WIDTH)
        self.i16: ir.IntType = ir.IntType(16)
        self.i32: ir.IntType = ir.IntType(INT32_BIT_WIDTH)
        self.i64: ir.IntType = ir.IntType(INT64_BIT_WIDTH)

        # Unsigned types (same LLVM representation as signed)
        self.u8: ir.IntType = ir.IntType(INT8_BIT_WIDTH)
        self.u16: ir.IntType = ir.IntType(16)
        self.u32: ir.IntType = ir.IntType(INT32_BIT_WIDTH)
        self.u64: ir.IntType = ir.IntType(INT64_BIT_WIDTH)

        # Floating-point types
        self.f32: ir.Type = ir.FloatType()
        self.f64: ir.Type = ir.DoubleType()

        # Utility types
        self.i1: ir.IntType = ir.IntType(1)
        self.str_ptr: ir.PointerType = ir.PointerType(self.i8)
        self.void: ir.VoidType = ir.VoidType()

        # String fat pointer type: {i8* data, i32 size}
        self.string_struct: ir.LiteralStructType = self._create_string_struct_type()

        # Type mapping dictionary for O(1) lookups
        self._builtin_type_map: dict[BuiltinType, ir.Type] = {
            BuiltinType.I8: self.i8,
            BuiltinType.I16: self.i16,
            BuiltinType.I32: self.i32,
            BuiltinType.I64: self.i64,
            BuiltinType.U8: self.u8,
            BuiltinType.U16: self.u16,
            BuiltinType.U32: self.u32,
            BuiltinType.U64: self.u64,
            BuiltinType.F32: self.f32,
            BuiltinType.F64: self.f64,
            BuiltinType.BOOL: self.i8,
            BuiltinType.STRING: self.string_struct,
            BuiltinType.BLANK: self.i32,
            BuiltinType.STDIN: self.str_ptr,
            BuiltinType.STDOUT: self.str_ptr,
            BuiltinType.STDERR: self.str_ptr,
            BuiltinType.FILE: self.str_ptr,
        }

    def _create_string_struct_type(self) -> ir.LiteralStructType:
        """Create LLVM struct type for fat pointer strings: {i8* data, i32 size}"""
        return ir.LiteralStructType([
            ir.PointerType(self.i8),
            self.i32,
        ])

    def ll_type(self, t: Ty) -> ir.Type:
        """Map language type to corresponding LLVM IR type.

        Provides the mapping from Sushi language types to their LLVM IR
        representations used in code generation. Uses dictionary lookup for
        builtin types (O(1)) and match for complex types.

        Args:
            t: The language type to convert.

        Returns:
            The corresponding LLVM IR type.

        Raises:
            TypeError: If the language type is not supported.
        """
        # Fast path: O(1) lookup for builtin types
        if isinstance(t, BuiltinType):
            llvm_type = self._builtin_type_map.get(t)
            if llvm_type is not None:
                return llvm_type
            raise_internal_error("CE0018", type=str(t))

        # Complex types require special handling
        match t:
            case ArrayType():
                # Map ArrayType to LLVM array: [N x element_type]
                element_type = self.ll_type(t.base_type)
                return ir.ArrayType(element_type, t.size)
            case DynamicArrayType():
                # Map DynamicArrayType to LLVM struct: {i32 len, i32 cap, T* data}
                element_type = self.ll_type(t.base_type)
                return self._create_dynamic_array_struct_type(element_type)
            case StructType():
                # Map StructType to LLVM struct: {field1_type, field2_type, ...}
                return self._get_struct_type(t)
            case EnumType():
                # Map EnumType to LLVM tagged union struct: {i32 tag, [union of variant data]}
                return self._get_enum_type(t)
            case ResultType():
                # Map ResultType to Result<T, E> enum
                # Create/get the corresponding enum via ensure_result_type_in_table
                from backend.generics.results import ensure_result_type_in_table
                result_enum = ensure_result_type_in_table(self.enum_table, t.ok_type, t.err_type)
                if result_enum:
                    return self._get_enum_type(result_enum)
                else:
                    # This should never happen if ensure_result_type_in_table is working
                    raise_internal_error("CE0046", type=f"Result<{t.ok_type}, {t.err_type}>")
            case IteratorType():
                # Map IteratorType to LLVM struct based on underlying collection type
                return self._create_iterator_struct_type(t)
            case ReferenceType():
                # Map ReferenceType to LLVM pointer: T*
                # References are zero-cost abstractions that compile to pointers
                referenced_llvm_type = self.ll_type(t.referenced_type)
                return ir.PointerType(referenced_llvm_type)
            case PointerType():
                # Map PointerType to LLVM pointer: T*
                # Pointers are heap-allocated memory used by Own<T>
                pointee_llvm_type = self.ll_type(t.pointee_type)
                return ir.PointerType(pointee_llvm_type)
            case UnknownType():
                # UnknownType might be a struct or enum type that needs resolution
                resolved = resolve_unknown_type(
                    t, self.struct_table.by_name, self.enum_table.by_name
                )
                if isinstance(resolved, StructType):
                    return self._get_struct_type(resolved)
                return self._get_enum_type(resolved)
            case _:
                # Check if this is a TypeParameter (should not reach codegen)
                from semantics.generics.types import TypeParameter
                if isinstance(t, TypeParameter):
                    raise_internal_error("CE0045", type=t.name)

                # Check if this is a GenericTypeRef using shared helper
                resolved = resolve_generic_type_ref(
                    t, self.struct_table.by_name, self.enum_table.by_name
                )
                if resolved is not None:
                    if isinstance(resolved, ResultType):
                        return self.ll_type(resolved)
                    if isinstance(resolved, StructType):
                        return self._get_struct_type(resolved)
                    return self._get_enum_type(resolved)
                raise_internal_error("CE0022", type=str(t))

    def _create_dynamic_array_struct_type(self, element_type: ir.Type) -> ir.LiteralStructType:
        """Create LLVM struct type for dynamic arrays: {i32 len, i32 cap, T* data}"""
        return ir.LiteralStructType([
            self.i32,
            self.i32,
            ir.PointerType(element_type),
        ])

    def _create_iterator_struct_type(self, iterator_type: IteratorType) -> ir.LiteralStructType:
        """Create LLVM struct type for Iterator<T>.

        The structure: {i32 current_index, i32 length, T* data_ptr}
        """
        element_type = self.ll_type(iterator_type.element_type)
        return ir.LiteralStructType([
            self.i32,
            self.i32,
            ir.PointerType(element_type),
        ])

    def _get_struct_type(self, struct_type: StructType) -> ir.LiteralStructType:
        """Create LLVM struct type for user-defined structs with caching."""
        # Check cache first
        cached = self.cache.get_struct(struct_type.name)
        if cached is not None:
            return cached

        # Special handling for HashMap<K, V>
        if struct_type.name.startswith("HashMap<"):
            return self._create_hashmap_struct_type(struct_type)

        # Special handling for List<T>
        if struct_type.name.startswith("List<"):
            return self._create_list_struct_type(struct_type)

        # For recursive structs, cache a placeholder first to break the cycle
        llvm_struct = ir.LiteralStructType([])
        self.cache.cache_struct(struct_type.name, llvm_struct)

        # Compute field types (may recursively reference the cached struct)
        field_types = []
        for field_name, field_type in struct_type.fields:
            field_types.append(self.ll_type(field_type))

        # Replace placeholder with actual struct type
        llvm_struct = ir.LiteralStructType(field_types)
        self.cache.cache_struct(struct_type.name, llvm_struct)
        return llvm_struct

    def _create_hashmap_struct_type(self, struct_type: StructType) -> ir.LiteralStructType:
        """Create LLVM struct type for HashMap<K, V>."""
        from stdlib.generics.collections.hashmap.types import extract_key_value_types, get_entry_type

        # Need TypeSystemWrapper for generic helpers
        from backend.llvm_types import TypeSystemWrapper
        wrapper = TypeSystemWrapper(self, self.struct_table, self.enum_table)

        key_type, value_type = extract_key_value_types(struct_type, wrapper)
        entry_type = get_entry_type(wrapper, key_type, value_type)

        # HashMap LLVM struct: {Entry<K,V>[], i32 size, i32 capacity, i32 tombstones}
        buckets_type = ir.LiteralStructType([
            self.i32,
            self.i32,
            ir.PointerType(entry_type),
        ])
        llvm_struct = ir.LiteralStructType([
            buckets_type,
            self.i32,
            self.i32,
            self.i32,
        ])

        self.cache.cache_struct(struct_type.name, llvm_struct)
        return llvm_struct

    def _create_list_struct_type(self, struct_type: StructType) -> ir.LiteralStructType:
        """Create LLVM struct type for List<T>."""
        from backend.generics.list.types import extract_element_type, get_list_llvm_type

        # Need TypeSystemWrapper for generic helpers
        from backend.llvm_types import TypeSystemWrapper
        wrapper = TypeSystemWrapper(self, self.struct_table, self.enum_table)

        element_type = extract_element_type(struct_type, wrapper)
        llvm_struct = get_list_llvm_type(wrapper, element_type)

        self.cache.cache_struct(struct_type.name, llvm_struct)
        return llvm_struct

    def _get_enum_type(self, enum_type: EnumType) -> ir.LiteralStructType:
        """Create LLVM struct type for enum (tagged union) with caching."""
        # Check cache first
        cached = self.cache.get_enum(enum_type.name)
        if cached is not None:
            return cached

        # Calculate the maximum size needed for variant data using shared helper
        from backend.types.core.sizing import TypeSizing
        sizing = TypeSizing(self.struct_table, self.enum_table)
        max_size = calculate_max_variant_size(enum_type, sizing.get_type_size_bytes)

        # Create tagged union: {i32 tag, [max_size x i8] data}
        # Ensure minimum 1 byte for data array to match sizing.py calculation
        data_size = max(max_size, 1)
        llvm_enum = ir.LiteralStructType([
            self.i32,
            ir.ArrayType(self.i8, data_size),
        ])

        # Cache for reuse
        self.cache.cache_enum(enum_type.name, llvm_enum)
        return llvm_enum
