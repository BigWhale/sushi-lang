"""Type resolution utilities for UnknownType to StructType/EnumType conversion."""
from __future__ import annotations
from typing import Callable, Dict, Optional, Tuple, TYPE_CHECKING
from sushi_lang.semantics.generics.interned import interned_name

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type, StructType, EnumType
    from sushi_lang.semantics.generics.types import GenericTypeRef


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
        """Every type argument resolved through `type_walk.map_named_types`.

        A descent of its own entered an array element and a `GenericTypeRef`'s arguments
        alone, so a name in a function type's arms came back unresolved (#791).
        """
        from sushi_lang.semantics.type_walk import map_named_types

        resolve = name_resolver(self.struct_table, self.enum_table)
        return tuple(map_named_types(arg, resolve) for arg in type_args)

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


# One resolution of each `GenericTypeRef` node, keyed by the node's id: the entry
# keeps the node, so the id cannot be used again while the memo lives.
_RefMemo = Dict[int, Tuple['GenericTypeRef', Optional['Type'], Tuple['Type', ...]]]


def name_resolver(
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
) -> Callable[['Type'], 'Type']:
    """`resolve_unknown_type` for one walk or one map over a type.

    The walk and the map call it on EACH node, and the resolution of a node resolves every
    node below it. The memo lets each node be resolved once, so the time is linear in the
    depth of the type, not polynomial. The tables do not change during one walk, so the
    memo lives for one walk only.
    """
    memo: _RefMemo = {}

    def resolve(ty: 'Type') -> 'Type':
        return resolve_unknown_type(ty, struct_table, enum_table, memo=memo)

    return resolve


def resolve_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
    *,
    memo: Optional[_RefMemo] = None,
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
        entry, _ = _resolve_generic_ref(ty, struct_table, enum_table, memo)
        if entry is not None:
            return entry

    return ty


def _resolve_generic_ref(
    ty: 'GenericTypeRef',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
    memo: Optional[_RefMemo] = None,
) -> Tuple[Optional['Type'], Tuple['Type', ...]]:
    """The table entry a `GenericTypeRef` names, and its arguments resolved by name.

    NAME-level resolution only, so the shallow resolver rather than
    `resolve_type_recursively`: a named type's str() is its name, so walking fields
    cannot change the mangled name but CAN cycle (#240's RecursionError).

    Each argument is resolved ONCE, and both callers use this one result. A second
    descent into the same arguments doubles the work at each level of nesting, and the
    time is then exponential in the depth of the written type.
    """
    if memo is not None:
        known = memo.get(id(ty))
        if known is not None:
            return known[1], known[2]
    args = tuple(_resolve_type_name(arg, struct_table, enum_table, memo)
                 for arg in ty.type_args)
    concrete_name = interned_name(ty.base_name, args)

    entry: Optional['Type'] = None
    if concrete_name in struct_table:
        entry = struct_table[concrete_name]
    elif concrete_name in enum_table:
        entry = enum_table[concrete_name]
    if memo is not None:
        memo[id(ty)] = (ty, entry, args)
    return entry, args


def _resolve_type_name(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
    memo: Optional[_RefMemo] = None,
) -> 'Type':
    """Resolve a type far enough to spell its NAME, and no further."""
    from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(ty, GenericTypeRef):
        entry, args = _resolve_generic_ref(ty, struct_table, enum_table, memo)
        if entry is not None:
            return entry
        if args != ty.type_args:
            return GenericTypeRef(base_name=ty.base_name, type_args=args)
        return ty

    resolved = resolve_unknown_type(ty, struct_table, enum_table, memo=memo)

    if isinstance(resolved, ArrayType):
        base = _resolve_type_name(resolved.base_type, struct_table, enum_table, memo)
        if base != resolved.base_type:
            return ArrayType(base_type=base, size=resolved.size)
    elif isinstance(resolved, DynamicArrayType):
        base = _resolve_type_name(resolved.base_type, struct_table, enum_table, memo)
        if base != resolved.base_type:
            return DynamicArrayType(base_type=base)

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

    return map_named_types(ty, name_resolver(struct_table, enum_table))


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

    resolve = name_resolver(struct_table, enum_table)

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

    from sushi_lang.semantics.generics.type_strings import strip_grouping
    type_str = strip_grouping(type_str.strip())

    if type_str == "~":
        return BuiltinType.BLANK

    for word, mode in (("peek ", BorrowMode.PEEK), ("poke ", BorrowMode.POKE)):
        if type_str.startswith(word):
            referent = parse_type_string(type_str[len(word):], struct_table, enum_table)
            return ReferenceType(referenced_type=referent, mutability=mode)

    # Before the array branch: a function type's return may end with "[]".
    if type_str.startswith("fn(") or type_str.startswith("fn ("):
        from sushi_lang.semantics.generics.type_strings import parse_function_type_string
        return parse_function_type_string(
            type_str, lambda text: parse_type_string(text, struct_table, enum_table))

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
