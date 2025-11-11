"""
LLVM type system management for the Sushi language compiler.

This module handles the mapping between Sushi language types and LLVM IR types,
providing type constants and utilities for type inference and conversion.
"""
from __future__ import annotations

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from semantics.typesys import Type as Ty, BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType, UnknownType, ResultType, IteratorType, ReferenceType, PointerType
from internals.errors import raise_internal_error


class TypeSystemWrapper:
    """Minimal codegen wrapper for generic type helpers.

    Used by HashMap and List type extraction functions that need access
    to the type system and tables but don't need full codegen functionality.
    """
    def __init__(self, types_system, struct_table, enum_table):
        self.types = types_system
        self.struct_table = struct_table
        self.enum_table = enum_table


class LLVMTypeSystem:
    """Manages LLVM type mapping and type-related operations."""

    def __init__(self, struct_table: 'StructTable | None' = None, enum_table: 'EnumTable | None' = None) -> None:
        """Initialize LLVM type system with standard type mappings.

        Creates the standard LLVM IR types used throughout the compiler:
        - Integer types: i8, i16, i32, i64 for signed integers
        - Unsigned types: u8, u16, u32, u64 for unsigned integers
        - Floating types: f32, f64 for floating-point numbers
        - i8 for language booleans
        - i8* for language strings
        - i1 for internal conditional operations
        - void for FFI and noreturn functions

        Args:
            struct_table: Optional struct table for resolving UnknownType to StructType.
            enum_table: Optional enum table for resolving enum types.
        """
        from semantics.passes.collect import StructTable, EnumTable
        self.struct_table = struct_table or StructTable()
        self.enum_table = enum_table or EnumTable()
        # Integer types
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

        # Struct type cache: maps struct name to LLVM struct type
        self._struct_cache: dict[str, ir.LiteralStructType] = {}

        # Enum type cache: maps enum name to LLVM tagged union struct type
        self._enum_cache: dict[str, ir.LiteralStructType] = {}

        # String fat pointer type: {i8* data, i32 size}
        self.string_struct: ir.LiteralStructType = self.get_string_struct_type()

        # Type mapping dictionary for O(1) lookups - populated after base types are created
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
            BuiltinType.STDIN: self.str_ptr,   # stdin handle (FILE* as i8*)
            BuiltinType.STDOUT: self.str_ptr,  # stdout handle (FILE* as i8*)
            BuiltinType.STDERR: self.str_ptr,  # stderr handle (FILE* as i8*)
            BuiltinType.FILE: self.str_ptr,    # file handle (FILE* as i8* opaque pointer)
        }

        # Reverse type mapping for O(1) LLVM -> language type conversion
        # Note: i8 is ambiguous (could be i8, u8, or bool) - default to i8
        self._llvm_to_lang_type_map: dict[ir.Type, str] = {
            self.i8: "i8",   # Ambiguous: could be i8, u8, or bool
            self.i16: "i16",
            self.i32: "i32",
            self.i64: "i64",
            self.f32: "f32",
            self.f64: "f64",
        }

        # Cache for complex type mappings (arrays, dynamic arrays, etc.)
        # Key: LLVM type, Value: language type string
        self._llvm_to_lang_type_cache: dict[ir.Type, str] = {}

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
                return self.get_dynamic_array_struct_type(element_type)
            case StructType():
                # Map StructType to LLVM struct: {field1_type, field2_type, ...}
                return self.get_struct_type(t)
            case EnumType():
                # Map EnumType to LLVM tagged union struct: {i32 tag, [union of variant data]}
                return self.get_enum_type(t)
            case ResultType():
                # Map ResultType to monomorphized Result<T> enum
                # Result<T> is now a regular enum, so look it up in the enum table
                result_enum_name = f"Result<{t.ok_type}>"
                if result_enum_name in self.enum_table.by_name:
                    result_enum = self.enum_table.by_name[result_enum_name]
                    return self.get_enum_type(result_enum)
                else:
                    # This should never happen if monomorphization is working correctly
                    raise_internal_error("CE0046", type=str(t.ok_type))
            case IteratorType():
                # Map IteratorType to LLVM struct based on underlying collection type
                return self.get_iterator_struct_type(t)
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
                if t.name in self.struct_table.by_name:
                    struct_type = self.struct_table.by_name[t.name]
                    return self.get_struct_type(struct_type)
                elif t.name in self.enum_table.by_name:
                    enum_type = self.enum_table.by_name[t.name]
                    return self.get_enum_type(enum_type)
                raise_internal_error("CE0020", type=t.name)
            case _:
                # Check if this is a TypeParameter (should not reach codegen)
                from semantics.generics.types import TypeParameter
                if isinstance(t, TypeParameter):
                    raise_internal_error("CE0045", type=t.name)

                # Check if this is a GenericTypeRef
                from semantics.generics.types import GenericTypeRef
                if isinstance(t, GenericTypeRef):
                    # Resolve GenericTypeRef to monomorphized enum or struct type
                    # Build type name: Maybe<i32> -> "Maybe<i32>", Box<i32> -> "Box<i32>"
                    type_args_str = ", ".join(str(arg) for arg in t.type_args)
                    concrete_name = f"{t.base_name}<{type_args_str}>"

                    # Check if it's a monomorphized enum
                    if concrete_name in self.enum_table.by_name:
                        enum_type = self.enum_table.by_name[concrete_name]
                        return self.get_enum_type(enum_type)

                    # Check if it's a monomorphized struct
                    if concrete_name in self.struct_table.by_name:
                        struct_type = self.struct_table.by_name[concrete_name]
                        return self.get_struct_type(struct_type)

                    raise_internal_error("CE0045", type=concrete_name)
                raise_internal_error("CE0022", type=str(t))

    def infer_llvm_type_from_value(self, value: ir.Value) -> ir.Type:
        """Infer LLVM type from a runtime value for method dispatch.

        Used primarily for extension method resolution where we need to
        determine the receiver type from an LLVM value.

        Args:
            value: The LLVM value to analyze.

        Returns:
            The LLVM type of the value.
        """
        return value.type

    def map_llvm_to_language_type(self, llvm_type: ir.Type) -> str:
        """Map LLVM type back to language type name for method resolution.

        Converts LLVM IR types back to language type names, primarily used
        for generating extension method function names. Uses a pre-built
        dictionary for O(1) lookup on simple types and caches complex types.

        Args:
            llvm_type: The LLVM IR type to map.

        Returns:
            The corresponding language type name.

        Raises:
            TypeError: If the LLVM type cannot be mapped to a language type.
        """
        # Check cache first (covers both simple types and previously computed complex types)
        if llvm_type in self._llvm_to_lang_type_cache:
            return self._llvm_to_lang_type_cache[llvm_type]

        # Fast path: Direct type mapping (O(1) lookup)
        if llvm_type in self._llvm_to_lang_type_map:
            return self._llvm_to_lang_type_map[llvm_type]

        # Compute and cache complex types
        result = None

        # String type check (fat pointer struct)
        if self.is_string_type(llvm_type):
            result = "string"

        # Array types (require recursion)
        elif isinstance(llvm_type, ir.ArrayType):
            element_name = self.map_llvm_to_language_type(llvm_type.element)
            result = f"{element_name}[{llvm_type.count}]"

        # Dynamic array types
        elif self.is_dynamic_array_type(llvm_type):
            data_field = llvm_type.elements[2]  # T*
            element_type = data_field.pointee
            element_name = self.map_llvm_to_language_type(element_type)
            result = f"{element_name}[]"

        # Pointer to dynamic array (from GEP on struct fields)
        elif isinstance(llvm_type, ir.PointerType) and self.is_dynamic_array_type(llvm_type.pointee):
            pointee = llvm_type.pointee
            data_field = pointee.elements[2]  # T*
            element_type = data_field.pointee
            element_name = self.map_llvm_to_language_type(element_type)
            result = f"{element_name}[]"

        if result is not None:
            # Cache the result for future lookups
            self._llvm_to_lang_type_cache[llvm_type] = result
            return result

        raise_internal_error("CE0019", llvm_type=str(llvm_type))

    def is_string_type(self, llvm_type: ir.Type) -> bool:
        """Check if LLVM type represents a string (fat pointer struct).

        Utility method to identify string types for special handling
        in operations like comparisons and printing.

        Args:
            llvm_type: The LLVM type to check.

        Returns:
            True if the type is a string fat pointer struct, False otherwise.
        """
        if not isinstance(llvm_type, ir.LiteralStructType):
            return False

        # Check struct layout: {i8*, i32}
        elements = llvm_type.elements
        if len(elements) != 2:
            return False

        # Check field types
        return (
            isinstance(elements[0], ir.PointerType) and
            isinstance(elements[0].pointee, ir.IntType) and
            elements[0].pointee.width == 8 and
            elements[1] == self.i32
        )

    def is_integer_type(self, llvm_type: ir.Type, width: int | None = None) -> bool:
        """Check if LLVM type is an integer with optional width constraint.

        Utility method to identify integer types, optionally checking for
        a specific bit width.

        Args:
            llvm_type: The LLVM type to check.
            width: Optional specific bit width to match.

        Returns:
            True if the type is an integer of the specified width, False otherwise.
        """
        if not isinstance(llvm_type, ir.IntType):
            return False
        if width is not None:
            return llvm_type.width == width
        return True

    def get_string_struct_type(self) -> ir.LiteralStructType:
        """
        Create LLVM struct type for fat pointer strings: {i8* data, i32 size}

        Returns:
            LLVM struct type representing the string fat pointer.
        """
        return ir.LiteralStructType([
            ir.PointerType(self.i8),  # data: i8*
            self.i32,                 # size: i32
        ])

    def get_dynamic_array_struct_type(self, element_type: ir.Type) -> ir.LiteralStructType:
        """
        Create LLVM struct type for dynamic arrays: {i32 len, i32 cap, T* data}

        Args:
            element_type: The LLVM type of array elements.

        Returns:
            LLVM struct type representing the dynamic array.
        """
        return ir.LiteralStructType([
            self.i32,                        # len: i32
            self.i32,                        # cap: i32
            ir.PointerType(element_type)     # data: T*
        ])

    def get_struct_type(self, struct_type: StructType) -> ir.LiteralStructType:
        """
        Create LLVM struct type for user-defined structs.

        Uses caching to ensure the same LLVM struct type is reused for the same
        language struct type, which is important for type equality in LLVM.

        Args:
            struct_type: The language struct type.

        Returns:
            LLVM struct type with fields matching the struct definition.
        """
        # Check cache first
        if struct_type.name in self._struct_cache:
            return self._struct_cache[struct_type.name]

        # Special handling for HashMap<K, V> - use custom LLVM type with Entry<K,V>[]
        if struct_type.name.startswith("HashMap<"):
            from backend.generics.hashmap.types import extract_key_value_types, get_entry_type

            wrapper = TypeSystemWrapper(self, self.struct_table, self.enum_table)
            key_type, value_type = extract_key_value_types(struct_type, wrapper)
            entry_type = get_entry_type(wrapper, key_type, value_type)

            # HashMap LLVM struct: {Entry<K,V>[], i32 size, i32 capacity, i32 tombstones}
            buckets_type = ir.LiteralStructType([
                self.i32,                       # len
                self.i32,                       # cap
                ir.PointerType(entry_type)      # data (Entry<K,V>*)
            ])
            llvm_struct = ir.LiteralStructType([
                buckets_type,      # buckets: Entry<K,V>[]
                self.i32,          # size
                self.i32,          # capacity
                self.i32,          # tombstones
            ])

            self._struct_cache[struct_type.name] = llvm_struct
            return llvm_struct

        # Special handling for List<T> - use custom LLVM type
        if struct_type.name.startswith("List<"):
            from backend.generics.list.types import extract_element_type, get_list_llvm_type

            wrapper = TypeSystemWrapper(self, self.struct_table, self.enum_table)
            element_type = extract_element_type(struct_type, wrapper)
            llvm_struct = get_list_llvm_type(wrapper, element_type)

            self._struct_cache[struct_type.name] = llvm_struct
            return llvm_struct

        # For recursive structs (e.g., struct Node with Own<Node> field), we need to
        # cache a placeholder first to break the cycle, then compute field types.
        # Create an opaque placeholder struct and cache it immediately
        llvm_struct = ir.LiteralStructType([])  # Empty placeholder
        self._struct_cache[struct_type.name] = llvm_struct

        # Now compute field types (this may recursively reference the cached struct)
        field_types = []
        for field_name, field_type in struct_type.fields:
            field_types.append(self.ll_type(field_type))

        # Replace the placeholder with the actual struct type
        llvm_struct = ir.LiteralStructType(field_types)
        self._struct_cache[struct_type.name] = llvm_struct
        return llvm_struct

    def get_enum_type(self, enum_type: EnumType) -> ir.LiteralStructType:
        """
        Create LLVM struct type for enum (tagged union).

        Enums are represented as: {i32 tag, [max_size x i8] data}
        where:
        - tag is the variant discriminant (0, 1, 2, ...)
        - data is a byte array large enough to hold any variant's associated data

        For unit variants (no associated data), the data array is still present but unused.

        Uses caching to ensure the same LLVM struct type is reused for the same
        language enum type, which is important for type equality in LLVM.

        Args:
            enum_type: The language enum type.

        Returns:
            LLVM struct type representing the tagged union.
        """
        # Check cache first
        if enum_type.name in self._enum_cache:
            return self._enum_cache[enum_type.name]

        # Calculate the maximum size needed for variant data
        max_size = 0
        for variant in enum_type.variants:
            if variant.associated_types:
                # Calculate size of this variant's data (sum of field sizes)
                # Use get_type_size_bytes() which properly handles all semantic types
                # including nested enums and structs
                variant_size = sum(self.get_type_size_bytes(t) for t in variant.associated_types)
                max_size = max(max_size, variant_size)

        # Allow zero-sized arrays for enums with no associated data
        # (matches stdlib ABI which uses {i32, [0 x i8]} for unit enums)
        # max_size can be 0 for enums like SeekFrom, FileError, etc.

        # Create tagged union: {i32 tag, [max_size x i8] data}
        llvm_enum = ir.LiteralStructType([
            self.i32,                           # tag (discriminant)
            ir.ArrayType(self.i8, max_size)     # data (raw bytes for variant payload)
        ])

        # Cache for reuse
        self._enum_cache[enum_type.name] = llvm_enum
        return llvm_enum

    def get_iterator_struct_type(self, iterator_type: IteratorType) -> ir.LiteralStructType:
        """
        Create LLVM struct type for Iterator<T>.

        The structure varies based on what we're iterating over:
        - For all iterators: {i32 current_index, i32 length/capacity, T* data_ptr}

        This representation works for both fixed and dynamic arrays.

        Args:
            iterator_type: The iterator type (contains element_type).

        Returns:
            LLVM struct type representing Iterator<T>.
        """
        element_type = self.ll_type(iterator_type.element_type)
        return ir.LiteralStructType([
            self.i32,                           # current_index: i32
            self.i32,                           # length: i32
            ir.PointerType(element_type)        # data_ptr: T*
        ])

    def get_dynamic_array_len_ptr(self, builder: ir.IRBuilder, array_ptr: ir.Value) -> ir.Value:
        """
        Get pointer to the 'len' field of a dynamic array struct.

        Args:
            builder: LLVM IR builder.
            array_ptr: Pointer to the dynamic array struct.

        Returns:
            Pointer to the len field (i32*).

        Note:
            This method delegates to backend.gep_utils for centralized GEP logic.
        """
        zero = ir.Constant(self.i32, 0)
        field_idx = ir.Constant(self.i32, 0)
        return builder.gep(array_ptr, [zero, field_idx], name="len_ptr")

    def get_dynamic_array_cap_ptr(self, builder: ir.IRBuilder, array_ptr: ir.Value) -> ir.Value:
        """
        Get pointer to the 'cap' field of a dynamic array struct.

        Args:
            builder: LLVM IR builder.
            array_ptr: Pointer to the dynamic array struct.

        Returns:
            Pointer to the cap field (i32*).

        Note:
            This method delegates to backend.gep_utils for centralized GEP logic.
        """
        zero = ir.Constant(self.i32, 0)
        field_idx = ir.Constant(self.i32, 1)
        return builder.gep(array_ptr, [zero, field_idx], name="cap_ptr")

    def get_dynamic_array_data_ptr(self, builder: ir.IRBuilder, array_ptr: ir.Value) -> ir.Value:
        """
        Get pointer to the 'data' field of a dynamic array struct.

        Args:
            builder: LLVM IR builder.
            array_ptr: Pointer to the dynamic array struct.

        Returns:
            Pointer to the data field (T**).

        Note:
            This method delegates to backend.gep_utils for centralized GEP logic.
        """
        zero = ir.Constant(self.i32, 0)
        field_idx = ir.Constant(self.i32, 2)
        return builder.gep(array_ptr, [zero, field_idx], name="data_ptr")

    def is_dynamic_array_type(self, llvm_type: ir.Type) -> bool:
        """
        Check if an LLVM type represents a dynamic array struct.

        Args:
            llvm_type: The LLVM type to check.

        Returns:
            True if the type is a dynamic array struct, False otherwise.
        """
        if not isinstance(llvm_type, ir.LiteralStructType):
            return False

        # Check struct layout: {i32, i32, T*}
        elements = llvm_type.elements
        if len(elements) != 3:
            return False

        # Check field types
        return (
            elements[0] == self.i32 and           # len: i32
            elements[1] == self.i32 and           # cap: i32
            isinstance(elements[2], ir.PointerType)  # data: T*
        )

    def get_type_size_bytes(self, semantic_type: Ty) -> int:
        """Get the size in bytes of a Sushi semantic type.

        This is the single source of truth for type sizes in the compiler.
        Returns size as a Python int for creating LLVM constants.

        Args:
            semantic_type: The Sushi language type.

        Returns:
            Size in bytes as Python int.

        Raises:
            TypeError: If the semantic type is not supported.
        """
        # Resolve UnknownType first
        if isinstance(semantic_type, UnknownType):
            if semantic_type.name in self.struct_table.by_name:
                semantic_type = self.struct_table.by_name[semantic_type.name]
            elif semantic_type.name in self.enum_table.by_name:
                semantic_type = self.enum_table.by_name[semantic_type.name]
            else:
                raise_internal_error("CE0020", type=semantic_type.name)

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
                    return 12  # Fat pointer: {i8* data, i32 size} = 8 + 4 = 12 bytes
                case BuiltinType.STDIN | BuiltinType.STDOUT | BuiltinType.STDERR | BuiltinType.FILE:
                    return 8  # Pointer size (64-bit)
                case _:
                    raise_internal_error("CE0021", type=str(semantic_type))

        # Complex types
        match semantic_type:
            case DynamicArrayType():
                # Dynamic array struct: {i32 len, i32 cap, T* data} = 4 + 4 + 8 = 16 bytes
                return 16
            case StructType():
                # Recursive calculation for structs
                return self._calculate_struct_size(semantic_type)
            case ArrayType():
                # Fixed array: element_size * count
                element_size = self.get_type_size_bytes(semantic_type.base_type)
                return element_size * semantic_type.size
            case EnumType():
                # Enum: {i32 tag, [max_size x i8] data}
                # Calculate max variant size
                max_variant_size = 0
                for variant in semantic_type.variants:
                    if variant.associated_types:
                        variant_size = sum(self.get_type_size_bytes(t) for t in variant.associated_types)
                        max_variant_size = max(max_variant_size, variant_size)
                # Tag (4 bytes) + data array (max_variant_size, minimum 1)
                return 4 + max(max_variant_size, 1)
            case IteratorType():
                # Iterator struct: {i32 current_index, i32 length, T* data_ptr} = 4 + 4 + 8 = 16 bytes
                return 16
            case ReferenceType():
                # References compile to pointers
                return 8  # 64-bit pointer
            case PointerType():
                # Pointers are always 8 bytes (64-bit)
                return 8
            case ResultType():
                # Result<T> is a monomorphized enum
                result_enum_name = f"Result<{semantic_type.ok_type}>"
                if result_enum_name in self.enum_table.by_name:
                    result_enum = self.enum_table.by_name[result_enum_name]
                    return self.get_type_size_bytes(result_enum)
                else:
                    raise_internal_error("CE0046", type=str(semantic_type.ok_type))
            case _:
                # Check if this is a GenericTypeRef (e.g., Maybe<i32>, Box<i32>, Result<Maybe<T>>)
                from semantics.generics.types import GenericTypeRef
                if isinstance(semantic_type, GenericTypeRef):
                    # Resolve GenericTypeRef to monomorphized enum or struct
                    # Build type name: Maybe<i32> -> "Maybe<i32>", Box<i32> -> "Box<i32>"
                    type_args_str = ", ".join(str(arg) for arg in semantic_type.type_args)
                    concrete_name = f"{semantic_type.base_name}<{type_args_str}>"

                    # Check if it's a monomorphized enum
                    if concrete_name in self.enum_table.by_name:
                        enum_type = self.enum_table.by_name[concrete_name]
                        return self.get_type_size_bytes(enum_type)

                    # Check if it's a monomorphized struct
                    if concrete_name in self.struct_table.by_name:
                        struct_type = self.struct_table.by_name[concrete_name]
                        return self.get_type_size_bytes(struct_type)

                    raise_internal_error("CE0045", type=concrete_name)
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
            field_align = self._get_type_alignment(field_type)

            # Track maximum alignment
            max_align = max(max_align, field_align)

            # Add padding to align this field
            if offset % field_align != 0:
                padding = field_align - (offset % field_align)
                offset += padding

            # Add the field size
            offset += field_size

        # Add final padding to align the entire struct
        if offset % max_align != 0:
            padding = max_align - (offset % max_align)
            offset += padding

        return offset

    def _get_type_alignment(self, semantic_type: Ty) -> int:
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
                    field_align = self._get_type_alignment(field_type)
                    max_align = max(max_align, field_align)
                return max_align
            case ArrayType():
                # Array aligned to element alignment
                return self._get_type_alignment(semantic_type.base_type)
            case EnumType():
                # Enum aligned to i32 tag (4 bytes)
                return 4
            case ReferenceType() | PointerType():
                # Pointers aligned to 8 bytes
                return 8
            case _:
                # Default to 8 bytes for unknown types (pointer alignment)
                return 8

    def get_type_size_constant(self, semantic_type: Ty) -> ir.Value:
        """Get the size in bytes of a semantic type as an LLVM i32 constant.

        Convenience wrapper around get_type_size_bytes() that returns an LLVM
        constant value ready for use in code generation.

        Args:
            semantic_type: The Sushi language type.

        Returns:
            LLVM i32 constant representing the size in bytes.

        Raises:
            TypeError: If the semantic type is not supported.
        """
        size_bytes = self.get_type_size_bytes(semantic_type)
        return ir.Constant(self.i32, size_bytes)