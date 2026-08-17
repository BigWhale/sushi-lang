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

        self.i8: ir.IntType = ir.IntType(INT8_BIT_WIDTH)
        self.i16: ir.IntType = ir.IntType(16)
        self.i32: ir.IntType = ir.IntType(INT32_BIT_WIDTH)
        self.i64: ir.IntType = ir.IntType(INT64_BIT_WIDTH)

        self.u8: ir.IntType = ir.IntType(INT8_BIT_WIDTH)
        self.u16: ir.IntType = ir.IntType(16)
        self.u32: ir.IntType = ir.IntType(INT32_BIT_WIDTH)
        self.u64: ir.IntType = ir.IntType(INT64_BIT_WIDTH)

        self.f32: ir.Type = ir.FloatType()
        self.f64: ir.Type = ir.DoubleType()

        self.i1: ir.IntType = ir.IntType(1)
        self.str_ptr: ir.PointerType = ir.PointerType(self.i8)
        self.void: ir.VoidType = ir.VoidType()

        # `{i8* data, i32 size, i8 owned}`. `owned` is the runtime discriminator (#145):
        # 0 = literal or borrow, never freed; 1 = heap, freed by RAII. LLVM sizeof stays 16,
        # so every embedding layout is byte-identical to the old `{i8*, i32}`.
        self.string_struct: ir.LiteralStructType = self._create_string_struct_type()

        # `{i8* fn_ptr, i8* env_ptr, i8* drop_ptr, i8* clone_ptr}`. `clone_ptr` is
        # `drop_ptr`'s twin: capture is erased from the `fn(...)` type, so a value cloned
        # through a field or container has no compile-time way to learn its env layout.
        self.closure_struct: ir.LiteralStructType = ir.LiteralStructType([
            self.str_ptr,  # fn_ptr  (opaque; bitcast to the real signature at call site)
            self.str_ptr,  # env_ptr (null when non-capturing)
            self.str_ptr,  # drop_ptr (null when non-capturing)
            self.str_ptr,  # clone_ptr (null when non-capturing)
        ])

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
        if isinstance(t, BuiltinType):
            llvm_type = self._builtin_type_map.get(t)
            if llvm_type is not None:
                return llvm_type
            raise_internal_error("CE0018", type=str(t))

        match t:
            case ArrayType():
                element_type = self.ll_type(t.base_type)
                return ir.ArrayType(element_type, t.size)
            case DynamicArrayType():
                element_type = self.ll_type(t.base_type)
                return self._create_dynamic_array_struct_type(element_type)
            case StructType():
                return self._get_struct_type(t)
            case EnumType():
                return self._get_enum_type(t)
            case IteratorType():
                return self._create_iterator_struct_type(t)
            case ReferenceType():
                referenced_llvm_type = self.ll_type(t.referenced_type)
                return ir.PointerType(referenced_llvm_type)
            case PointerType():
                pointee_llvm_type = self.ll_type(t.pointee_type)
                return ir.PointerType(pointee_llvm_type)
            case ForeignPtrType():
                return ir.PointerType(self.i8)
            case FunctionType():
                # The 4-word fat pointer. Capture is erased, so a non-capturing value
                # carries null env/drop/clone. The real callee signature is recovered from
                # the semantic FunctionType at the call site, not from this opaque type.
                return self.closure_struct
            case UnknownType():
                resolved = resolve_unknown_type(
                    t, self.struct_table.by_name, self.enum_table.by_name
                )
                if isinstance(resolved, StructType):
                    return self._get_struct_type(resolved)
                return self._get_enum_type(resolved)
            case _:
                from sushi_lang.semantics.generics.types import TypeParameter
                if isinstance(t, TypeParameter):
                    raise_internal_error("CE0045", type=t.name)

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
        cached = self.cache.get_struct(struct_type.name)
        if cached is not None:
            return cached

        # The builtin containers are anonymous LAYOUT DESCRIPTORS whose LLVM shape other
        # backend code builds directly, so they stay LITERAL and never take the identified
        # path below: an identified `%Own<i32>` would not equal `{i32*}` (#257).
        if struct_type.name.startswith("HashMap<"):
            return self._create_hashmap_struct_type(struct_type)

        if struct_type.name.startswith("List<"):
            return self._create_list_struct_type(struct_type)

        if struct_type.name.startswith("Own<") or struct_type.name.startswith("Entry<"):
            return self._create_builtin_literal_struct_type(struct_type)

        # A user struct is an LLVM IDENTIFIED type, which is what makes a self-reference
        # expressible (#257): `set_body` fills it IN PLACE, so a pointer taken during the
        # field walk below stays valid. A literal struct type is a structural VALUE with
        # nothing to fill in, so an embedded `{}` stayed empty and every element GEP
        # through it had stride ZERO.
        #
        # `struct_type.name` is used VERBATIM, `<...>` included. It must not be sanitised:
        # the name is the identity, so two monomorphizations colliding on one identified
        # type would silently share a layout.
        llvm_struct = self.context.get_identified_type(struct_type.name)
        self.cache.cache_struct(struct_type.name, llvm_struct)

        field_types = []
        for _field_name, field_type in struct_type.fields:
            field_types.append(self.ll_type(field_type))

        llvm_struct.set_body(*field_types)
        return llvm_struct

    def _create_hashmap_struct_type(self, struct_type: StructType) -> ir.LiteralStructType:
        """Create LLVM struct type for HashMap<K, V>."""
        from sushi_lang.backend.generics.hashmap.types import get_entry_type
        from sushi_lang.semantics.generics.hashmap import extract_key_value_types

        from sushi_lang.backend.llvm_types import TypeSystemWrapper
        wrapper = TypeSystemWrapper(self, self.struct_table, self.enum_table)

        key_type, value_type = extract_key_value_types(struct_type, wrapper)
        entry_type = get_entry_type(wrapper, key_type, value_type)

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

        self.cache.cache_enum(enum_type.name, llvm_enum)
        return llvm_enum
