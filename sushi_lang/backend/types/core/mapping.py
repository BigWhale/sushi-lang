"""Type mapping from Sushi semantic types to LLVM IR types."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable
    from sushi_lang.backend.types.core.caching import TypeCache

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.semantics.typesys import (
    Type as Ty,
    BuiltinType,
    ArrayType,
    DynamicArrayType,
    StructType,
    EnumType,
    UnknownType,
    IteratorType,
    ReferenceType,
    PointerType,
    ForeignPtrType,
    FunctionType,
)
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.types.core.resolution import resolve_unknown_type, resolve_generic_type_ref


class TypeMapper:
    """Maps Sushi semantic types to LLVM IR types."""

    def __init__(
        self,
        cache: TypeCache,
        struct_table: StructTable,
        enum_table: EnumTable,
        context: 'ir.Context | None' = None,
    ):
        """Initialize type mapper with caching and type tables."""
        self.cache = cache
        self.struct_table = struct_table
        self.enum_table = enum_table
        self.context = context if context is not None else ir.Context()

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

        # String fat pointer type: {i8* data, i32 size, i8 owned}
        # `owned` is the runtime ownership discriminator (issue #145): 0 = literal/borrow
        # (backed by a global or aliased, never freed), 1 = heap (malloc'd, freed by RAII).
        # Same role as the closure fat value's drop_ptr slot. LLVM sizeof stays 16 (already
        # alignment-padded), so enum/struct/Result layouts are byte-identical to the old
        # {i8*, i32} and FAT_POINTER_SIZE_BYTES stays 12.
        self.string_struct: ir.LiteralStructType = self._create_string_struct_type()

        # Closure/function-value fat pointer type:
        # {i8* fn_ptr, i8* env_ptr, i8* drop_ptr, i8* clone_ptr}
        # `clone_ptr` is `drop_ptr`'s twin and exists for the same reason: capture is
        # erased from the `fn(...)` type, so a value cloned through a struct field or a
        # container has no compile-time way to learn its own environment layout. Carrying
        # a type-erased env duplicator alongside the type-erased env destructor is what
        # lets a closure be deep-copied at all. Slots 0-2 keep their indices.
        self.closure_struct: ir.LiteralStructType = ir.LiteralStructType([
            self.str_ptr,  # fn_ptr  (opaque; bitcast to the real signature at call site)
            self.str_ptr,  # env_ptr (null when non-capturing)
            self.str_ptr,  # drop_ptr (null when non-capturing)
            self.str_ptr,  # clone_ptr (null when non-capturing)
        ])

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
        """Create LLVM struct type for fat pointer strings: {i8* data, i32 size, i8 owned}"""
        return ir.LiteralStructType([
            ir.PointerType(self.i8),
            self.i32,
            self.i8,  # owned: 1 = heap (RAII frees), 0 = literal/borrow (never freed)
        ])

    def ll_type(self, t: Ty) -> ir.Type:
        """Map language type to corresponding LLVM IR type."""
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
            case ForeignPtrType():
                # Map ForeignPtrType (`ptr`) to opaque LLVM i8* for the C ABI.
                return ir.PointerType(self.i8)
            case FunctionType():
                # Map a first-class function value to the 4-word fat pointer
                # {i8* fn_ptr, i8* env_ptr, i8* drop_ptr, i8* clone_ptr}. Capture is
                # erased: a non-capturing value carries null env/drop/clone; a closure
                # carries a heap env plus a type-erased destructor and duplicator. The
                # real callee signature
                # (Result<T,E>(i8* env, params)) is recovered from the semantic
                # FunctionType at the call site, not from this opaque LLVM type.
                return self.closure_struct
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
                from sushi_lang.semantics.generics.types import TypeParameter
                if isinstance(t, TypeParameter):
                    raise_internal_error("CE0045", type=t.name)

                # Check if this is a GenericTypeRef using shared helper
                resolved = resolve_generic_type_ref(
                    t, self.struct_table.by_name, self.enum_table.by_name
                )
                if resolved is not None:
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
        """Create LLVM struct type for Iterator<T>."""
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

        # The generic BUILTIN containers are anonymous LAYOUT DESCRIPTORS, not nominal
        # types, and each has a hand-written LLVM shape that other backend code builds
        # directly (`{T*}` in generics/own.py, `{K, V, u8}` in generics/hashmap/types.py).
        # They must keep matching those, so they stay literal and never take the identified
        # path below (#257) -- an identified `%Own<i32>` would not equal the `{i32*}` that
        # emit_own_alloc constructs, which is exactly the CE0017 wall this hit.
        if struct_type.name.startswith("HashMap<"):
            return self._create_hashmap_struct_type(struct_type)

        if struct_type.name.startswith("List<"):
            return self._create_list_struct_type(struct_type)

        if struct_type.name.startswith("Own<") or struct_type.name.startswith("Entry<"):
            return self._create_builtin_literal_struct_type(struct_type)

        # A user struct is an LLVM *identified* type: `%Name = type {...}`. That is what
        # makes a self-reference expressible (#257) -- `set_body` fills the type IN PLACE,
        # so a pointer taken to it during the field walk below stays valid and resolves to
        # the finished layout.
        #
        # This used to cache an empty `ir.LiteralStructType([])` placeholder, walk the
        # fields, then cache a NEW literal built from them. A literal struct type is a
        # structural VALUE, so there is nothing to fill in: re-caching replaced the cache
        # entry, but the `{}` the walk had already embedded into `{i32, i32, {}*}` stayed
        # empty forever. `struct Tree: List@(Tree) kids` came out as
        # `{i32, {i32, i32, {}*}}`, so every element GEP through it had stride ZERO and a
        # freshly computed `Tree[]` disagreed with the struct's own field type.
        #
        # `struct_type.name` is used verbatim, including the `<...>` of an interned generic
        # name (`Pair<i32, bool>`). It must NOT be sanitised or shortened: the name is the
        # identity, so two monomorphizations that collided on one identified type would
        # silently share a layout.
        llvm_struct = self.context.get_identified_type(struct_type.name)
        self.cache.cache_struct(struct_type.name, llvm_struct)

        # Compute field types (a self-reference resolves to the opaque handle above)
        field_types = []
        for _field_name, field_type in struct_type.fields:
            field_types.append(self.ll_type(field_type))

        llvm_struct.set_body(*field_types)
        return llvm_struct

    def _create_hashmap_struct_type(self, struct_type: StructType) -> ir.LiteralStructType:
        """Create LLVM struct type for HashMap<K, V>."""
        from sushi_lang.backend.generics.hashmap.types import get_entry_type
        from sushi_lang.semantics.generics.hashmap import extract_key_value_types

        # Need TypeSystemWrapper for generic helpers
        from sushi_lang.backend.llvm_types import TypeSystemWrapper
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
        from sushi_lang.backend.generics.list.types import extract_element_type, get_list_llvm_type

        # Need TypeSystemWrapper for generic helpers
        from sushi_lang.backend.llvm_types import TypeSystemWrapper
        wrapper = TypeSystemWrapper(self, self.struct_table, self.enum_table)

        element_type = extract_element_type(struct_type, wrapper)
        llvm_struct = get_list_llvm_type(wrapper, element_type)

        self.cache.cache_struct(struct_type.name, llvm_struct)
        return llvm_struct

    def _create_builtin_literal_struct_type(self, struct_type: StructType) -> ir.LiteralStructType:
        """Map a generic BUILTIN container's declared fields to a literal LLVM struct."""
        field_types = [self.ll_type(field_type) for _name, field_type in struct_type.fields]
        llvm_struct = ir.LiteralStructType(field_types)

        self.cache.cache_struct(struct_type.name, llvm_struct)
        return llvm_struct

    def _get_enum_type(self, enum_type: EnumType) -> ir.LiteralStructType:
        """Create LLVM struct type for enum (tagged union) with caching."""
        # Check cache first
        cached = self.cache.get_enum(enum_type.name)
        if cached is not None:
            return cached

        # Payload word count from the layout authority, so the array can never be
        # smaller than the aligned field offsets it must hold.
        from sushi_lang.backend.types.core.sizing import TypeSizing
        sizing = TypeSizing(self.struct_table, self.enum_table)
        word_count = sizing.enum_payload_word_count(enum_type)

        llvm_enum = ir.LiteralStructType([
            self.i32,
            ir.ArrayType(self.i64, word_count),
        ])

        # Cache for reuse
        self.cache.cache_enum(enum_type.name, llvm_enum)
        return llvm_enum
