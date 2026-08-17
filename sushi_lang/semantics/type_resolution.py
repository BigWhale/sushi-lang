"""Type resolution utilities for UnknownType to StructType/EnumType conversion."""
from __future__ import annotations
from dataclasses import replace
from typing import Dict, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type, StructType, EnumType


class TypeResolver:
    """Centralized type resolution with caching and validation."""

    def __init__(
        self,
        struct_table: Dict[str, 'StructType'],
        enum_table: Dict[str, 'EnumType']
    ):
        """Initialize resolver with type tables."""
        self.struct_table = struct_table
        self.enum_table = enum_table

    def resolve(self, ty: 'Type') -> 'Type':
        """Single entry point for all type resolution."""
        return resolve_unknown_type(ty, self.struct_table, self.enum_table)

    def resolve_recursively(self, ty: 'Type') -> 'Type':
        """Recursively resolve UnknownType in nested type structures."""
        return resolve_type_recursively(ty, self.struct_table, self.enum_table)

    def resolve_type_args(self, type_args: Tuple['Type', ...]) -> Tuple['Type', ...]:
        """Resolve all UnknownType instances in type_args tuple."""
        from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType
        from sushi_lang.semantics.generics.types import GenericTypeRef

        resolved_args = []
        for arg in type_args:
            resolved_arg = resolve_unknown_type(arg, self.struct_table, self.enum_table)

            if isinstance(resolved_arg, (ArrayType, DynamicArrayType)):
                resolved_base = resolve_unknown_type(
                    resolved_arg.base_type,
                    self.struct_table,
                    self.enum_table
                )
                if isinstance(resolved_arg, ArrayType):
                    resolved_arg = ArrayType(base_type=resolved_base, size=resolved_arg.size)
                else:
                    resolved_arg = DynamicArrayType(base_type=resolved_base)
            elif isinstance(resolved_arg, GenericTypeRef):
                resolved_nested_args = self.resolve_type_args(resolved_arg.type_args)
                resolved_arg = GenericTypeRef(
                    base_name=resolved_arg.base_name,
                    type_args=resolved_nested_args
                )

            resolved_args.append(resolved_arg)

        return tuple(resolved_args)

    def resolve_generic_type_ref(self, ty: 'Type') -> 'Type':
        """Resolve GenericTypeRef to monomorphized EnumType or StructType."""
        from sushi_lang.semantics.generics.types import GenericTypeRef

        if isinstance(ty, GenericTypeRef):
            type_args_str = ", ".join(str(arg) for arg in ty.type_args)
            concrete_name = f"{ty.base_name}<{type_args_str}>"

            if concrete_name in self.enum_table:
                return self.enum_table[concrete_name]

            if concrete_name in self.struct_table:
                return self.struct_table[concrete_name]

        return ty

    def contains_unresolvable(self, ty: 'Type', visited: Optional[Set[str]] = None) -> bool:
        """Check if a type contains UnknownType that cannot be resolved."""
        return contains_unresolvable_unknown_type(
            ty, self.struct_table, self.enum_table, visited
        )

    def contains_unresolvable_in_tuple(self, type_args: Tuple['Type', ...]) -> bool:
        """Check if any type in a tuple contains unresolvable UnknownType."""
        for arg in type_args:
            if self.contains_unresolvable(arg):
                return True
        return False


def resolve_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Resolve UnknownType or GenericTypeRef to StructType or EnumType if possible."""
    from sushi_lang.semantics.typesys import UnknownType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(ty, UnknownType):
        if ty.name in struct_table:
            return struct_table[ty.name]
        if ty.name in enum_table:
            return enum_table[ty.name]

    # Handle GenericTypeRef - resolve to concrete monomorphized type.
    # Result<T, E> takes the same path as every other generic: it is monomorphized into the
    # enum table like Maybe, so it resolves by concrete-name lookup. It used to be special-cased
    # into a ResultType here, which is NOT an EnumType -- so a Result from an annotation and a
    # Result from a call compared unequal (#184).
    elif isinstance(ty, GenericTypeRef):
        # NAME-level resolution only, so the shallow resolver rather than
        # `resolve_type_recursively`: a named type's str() is its name, so walking fields
        # cannot change the mangled name but CAN cycle (#240's RecursionError). The sibling
        # mangling sites use a bare str(arg) for the same reason.
        type_arg_strs = ", ".join(
            str(_resolve_type_name(arg, struct_table, enum_table))
            for arg in ty.type_args
        )
        concrete_name = f"{ty.base_name}<{type_arg_strs}>"

        if concrete_name in struct_table:
            return struct_table[concrete_name]
        if concrete_name in enum_table:
            return enum_table[concrete_name]

    return ty


def _resolve_type_name(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Resolve a type far enough to spell its NAME, and no further."""
    from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    resolved = resolve_unknown_type(ty, struct_table, enum_table)

    if isinstance(resolved, ArrayType):
        base = _resolve_type_name(resolved.base_type, struct_table, enum_table)
        if base != resolved.base_type:
            return ArrayType(base_type=base, size=resolved.size)
    elif isinstance(resolved, DynamicArrayType):
        base = _resolve_type_name(resolved.base_type, struct_table, enum_table)
        if base != resolved.base_type:
            return DynamicArrayType(base_type=base)
    elif isinstance(resolved, GenericTypeRef):
        args = tuple(
            _resolve_type_name(arg, struct_table, enum_table)
            for arg in resolved.type_args
        )
        if args != resolved.type_args:
            return GenericTypeRef(base_name=resolved.base_name, type_args=args)

    return resolved


def resolve_type_recursively(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
    visited: Optional[frozenset] = None
) -> 'Type':
    """Recursively resolve UnknownType in nested type structures.

    A named type resolves to its TABLE ENTRY and stops there: the table is the sole
    authority for its contents, so rebuilding one manufactures a second instance of a type
    that already exists (#240) and cannot terminate for a self-reference.
    See docs/design/type-identity.md.
    """
    from sushi_lang.semantics.typesys import (
        ArrayType, DynamicArrayType, StructType, EnumType, FunctionType
    )
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if visited is None:
        visited = frozenset()

    resolved_ty = resolve_unknown_type(ty, struct_table, enum_table)

    type_key = None
    if isinstance(resolved_ty, StructType):
        type_key = ("struct", resolved_ty.name)
    elif isinstance(resolved_ty, EnumType):
        type_key = ("enum", resolved_ty.name)
    if type_key is not None:
        if type_key in visited:
            return resolved_ty
        visited = visited | {type_key}

    if isinstance(resolved_ty, FunctionType):
        new_params = tuple(
            resolve_type_recursively(p, struct_table, enum_table, visited)
            for p in resolved_ty.param_types
        )
        new_ok = resolve_type_recursively(
            resolved_ty.ok_type, struct_table, enum_table, visited)
        new_err = resolve_type_recursively(
            resolved_ty.err_type, struct_table, enum_table, visited)
        if (new_params != resolved_ty.param_types or
                new_ok != resolved_ty.ok_type or
                new_err != resolved_ty.err_type):
            # `replace`, so `captures` and `param_modes` ride along. Building a fresh
            # FunctionType here dropped the declared `nom` of an annotated fn type (#368).
            return replace(resolved_ty, param_types=new_params, ok_type=new_ok,
                           err_type=new_err)
        return resolved_ty

    if isinstance(resolved_ty, ArrayType):
        resolved_base = resolve_type_recursively(
            resolved_ty.base_type, struct_table, enum_table, visited
        )
        if resolved_base != resolved_ty.base_type:
            return ArrayType(base_type=resolved_base, size=resolved_ty.size)

    elif isinstance(resolved_ty, DynamicArrayType):
        resolved_base = resolve_type_recursively(
            resolved_ty.base_type, struct_table, enum_table, visited
        )
        if resolved_base != resolved_ty.base_type:
            return DynamicArrayType(base_type=resolved_base)

    elif isinstance(resolved_ty, GenericTypeRef):
        resolved_args = tuple(
            resolve_type_recursively(arg, struct_table, enum_table, visited)
            for arg in resolved_ty.type_args
        )
        if resolved_args != resolved_ty.type_args:
            return GenericTypeRef(
                base_name=resolved_ty.base_name,
                type_args=resolved_args
            )

    return resolved_ty


def contains_unresolvable_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
    visited: Optional[Set[str]] = None
) -> bool:
    """Check if a type contains UnknownType that cannot be resolved."""
    from sushi_lang.semantics.typesys import (
        UnknownType, ArrayType, DynamicArrayType, StructType, EnumType, FunctionType
    )
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if visited is None:
        visited = set()

    resolved_ty = resolve_unknown_type(ty, struct_table, enum_table)

    if isinstance(resolved_ty, UnknownType):
        return True

    type_key = None
    if isinstance(resolved_ty, StructType):
        type_key = f"struct:{resolved_ty.name}"
    elif isinstance(resolved_ty, EnumType):
        type_key = f"enum:{resolved_ty.name}"

    if type_key and type_key in visited:
        return False

    if type_key:
        visited = visited | {type_key}

    if isinstance(resolved_ty, (ArrayType, DynamicArrayType)):
        return contains_unresolvable_unknown_type(
            resolved_ty.base_type, struct_table, enum_table, visited
        )

    elif isinstance(resolved_ty, GenericTypeRef):
        return any(
            contains_unresolvable_unknown_type(arg, struct_table, enum_table, visited)
            for arg in resolved_ty.type_args
        )

    elif isinstance(resolved_ty, StructType):
        return any(
            contains_unresolvable_unknown_type(field_type, struct_table, enum_table, visited)
            for _, field_type in resolved_ty.fields
        )

    elif isinstance(resolved_ty, EnumType):
        return any(
            contains_unresolvable_unknown_type(assoc_type, struct_table, enum_table, visited)
            for variant in resolved_ty.variants
            for assoc_type in variant.associated_types
        )

    elif isinstance(resolved_ty, FunctionType):
        return (
            any(
                contains_unresolvable_unknown_type(p, struct_table, enum_table, visited)
                for p in resolved_ty.param_types
            )
            or contains_unresolvable_unknown_type(resolved_ty.ok_type, struct_table, enum_table, visited)
            or contains_unresolvable_unknown_type(resolved_ty.err_type, struct_table, enum_table, visited)
        )

    return False


def parse_type_string(
    type_str: str,
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Parse a type string from a manifest file back to a Type object."""
    from sushi_lang.semantics.typesys import (
        BuiltinType, ArrayType, BorrowMode, DynamicArrayType, ReferenceType, UnknownType
    )

    type_str = type_str.strip()

    if type_str == "~":
        return BuiltinType.BLANK

    for word, mode in (("peek ", BorrowMode.PEEK), ("poke ", BorrowMode.POKE)):
        if type_str.startswith(word):
            referent = parse_type_string(type_str[len(word):], struct_table, enum_table)
            return ReferenceType(referenced_type=referent, mutability=mode)

    primitives = {
        "i8": BuiltinType.I8,
        "i16": BuiltinType.I16,
        "i32": BuiltinType.I32,
        "i64": BuiltinType.I64,
        "u8": BuiltinType.U8,
        "u16": BuiltinType.U16,
        "u32": BuiltinType.U32,
        "u64": BuiltinType.U64,
        "f32": BuiltinType.F32,
        "f64": BuiltinType.F64,
        "bool": BuiltinType.BOOL,
        "string": BuiltinType.STRING,
    }
    if type_str in primitives:
        return primitives[type_str]

    if type_str.endswith("[]"):
        base_str = type_str[:-2]
        base_type = parse_type_string(base_str, struct_table, enum_table)
        return DynamicArrayType(base_type=base_type)

    if type_str.endswith("]") and "[" in type_str:
        bracket_idx = type_str.rfind("[")
        size_str = type_str[bracket_idx+1:-1]
        base_str = type_str[:bracket_idx]
        if size_str.isdigit():
            base_type = parse_type_string(base_str, struct_table, enum_table)
            return ArrayType(base_type=base_type, size=int(size_str))

    if type_str in struct_table:
        return struct_table[type_str]

    if type_str in enum_table:
        return enum_table[type_str]

    return UnknownType(name=type_str)
