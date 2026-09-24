"""Type resolution utilities for UnknownType to StructType/EnumType conversion."""
from __future__ import annotations
from typing import Dict, Tuple, TYPE_CHECKING
from sushi_lang.semantics.generics.interned import interned_name

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
            concrete_name = interned_name(ty.base_name, ty.type_args)

            if concrete_name in self.enum_table:
                return self.enum_table[concrete_name]

            if concrete_name in self.struct_table:
                return self.struct_table[concrete_name]

        return ty

    def contains_unresolvable(self, ty: 'Type') -> bool:
        """Check if a type contains UnknownType that cannot be resolved."""
        return contains_unresolvable_unknown_type(ty, self.struct_table, self.enum_table)

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
        concrete_name = interned_name(
            ty.base_name,
            (_resolve_type_name(arg, struct_table, enum_table) for arg in ty.type_args))

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
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Recursively resolve UnknownType in nested type structures.

    One call into `type_walk.map_named_types`, which rebuilds through the arm table
    `walk_named_types` reads. A resolver with an arm set of its own had four arms against
    the walk's thirteen, and a name in a reference, a pointer or an iterator came back
    unresolved (#716, #718).

    A named type resolves to its TABLE ENTRY and stops there: the table is the sole
    authority for its contents, so rebuilding one manufactures a second instance of a type
    that already exists (#240) and cannot terminate for a self-reference.
    See docs/design/type-identity.md.
    """
    from sushi_lang.semantics.type_walk import map_named_types

    return map_named_types(
        ty, lambda held: resolve_unknown_type(held, struct_table, enum_table)
    )


def contains_unresolvable_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
) -> bool:
    """Does a name anywhere in the type reach no declaration?

    One question over `type_walk.walk_named_types`: the walk resolves each name it meets
    and enters the declaration, so a name that it yields and that still resolves to
    nothing is the unresolvable one (#724).
    """
    from sushi_lang.semantics.type_walk import walk_named_types
    from sushi_lang.semantics.typesys import UnknownType

    def resolve(held: 'Type') -> 'Type':
        return resolve_unknown_type(held, struct_table, enum_table)

    return any(
        isinstance(reached, UnknownType) and isinstance(resolve(reached), UnknownType)
        for reached in walk_named_types(ty, resolve=resolve)
    )


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

    # An instantiation the consumer has not interned yet -- `Box<i32>`, or an explicit
    # `Result<i32, MyErr>` return -- reads back as the GenericTypeRef the producer wrote
    # it from, so the instantiate pass can collect it and a Result is never wrapped
    # twice (#541, #543). An UnknownType of the whole spelling resolved only by luck,
    # when something else had already interned the name.
    if "<" in type_str and type_str.endswith(">"):
        from sushi_lang.semantics.generics.type_strings import split_type_arguments
        from sushi_lang.semantics.generics.types import GenericTypeRef
        open_at = type_str.index("<")
        args = split_type_arguments(type_str[open_at + 1:-1])
        return GenericTypeRef(
            base_name=type_str[:open_at].strip(),
            type_args=tuple(parse_type_string(a, struct_table, enum_table) for a in args))

    return UnknownType(name=type_str)
