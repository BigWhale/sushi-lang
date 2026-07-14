"""Type checking predicates and type sets for semantic analysis.

This module centralizes type predicates and type sets that were previously
scattered across types.py and type_visitor.py. Extracted as part of the
semantics directory refactoring (Phase 1, Task 1.4).
"""

from typing import Optional, Set
from sushi_lang.semantics.typesys import Type, BuiltinType


# === Type Sets ===

BUILTIN_INTEGER_TYPES: Set[BuiltinType] = {
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
}

BUILTIN_UNSIGNED_INTEGER_TYPES: Set[BuiltinType] = {
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
}

BUILTIN_FLOAT_TYPES: Set[BuiltinType] = {
    BuiltinType.F32, BuiltinType.F64
}

BUILTIN_NUMERIC_TYPES: Set[BuiltinType] = BUILTIN_INTEGER_TYPES | BUILTIN_FLOAT_TYPES

BUILTIN_STRING_CONVERTIBLE_TYPES: Set[BuiltinType] = BUILTIN_NUMERIC_TYPES | {
    BuiltinType.BOOL, BuiltinType.STRING
}


# === Type Predicates ===

def is_numeric_type(ty: Type) -> bool:
    """Check if a type is numeric (integer or floating-point).

    Args:
        ty: The type to check.

    Returns:
        True if the type is a numeric type (integer or float), False otherwise.

    Examples:
        >>> is_numeric_type(BuiltinType.I32)
        True
        >>> is_numeric_type(BuiltinType.F64)
        True
        >>> is_numeric_type(BuiltinType.STRING)
        False
    """
    return ty in BUILTIN_NUMERIC_TYPES


def is_integer_type(ty: Type) -> bool:
    """Check if a type is an integer type (signed or unsigned).

    Args:
        ty: The type to check.

    Returns:
        True if the type is an integer type, False otherwise.

    Examples:
        >>> is_integer_type(BuiltinType.I32)
        True
        >>> is_integer_type(BuiltinType.U8)
        True
        >>> is_integer_type(BuiltinType.F32)
        False
    """
    return ty in BUILTIN_INTEGER_TYPES


def is_float_type(ty: Type) -> bool:
    """Check if a type is a floating-point type.

    Args:
        ty: The type to check.

    Returns:
        True if the type is a floating-point type, False otherwise.

    Examples:
        >>> is_float_type(BuiltinType.F32)
        True
        >>> is_float_type(BuiltinType.F64)
        True
        >>> is_float_type(BuiltinType.I32)
        False
    """
    return ty in BUILTIN_FLOAT_TYPES


def is_unsigned_int(ty: Optional[Type]) -> bool:
    """Check if a type is an unsigned integer type (u8/u16/u32/u64).

    Args:
        ty: The type to check (None-safe).

    Returns:
        True if the type is an unsigned integer type, False otherwise.
    """
    return ty in BUILTIN_UNSIGNED_INTEGER_TYPES


def is_string_convertible(ty: Type) -> bool:
    """Check if a type can be converted to string in string interpolation.

    Supported types for string interpolation:
    - All numeric types (integers and floats)
    - Boolean type
    - String type

    Not supported:
    - Arrays (fixed or dynamic)
    - Structs
    - Enums (including Result<T>, Maybe<T>)
    - Iterator<T>
    - Blank type (~)
    - Special types (stdin, stdout, stderr, file)

    Args:
        ty: The type to check.

    Returns:
        True if the type can be converted to string, False otherwise.

    Examples:
        >>> is_string_convertible(BuiltinType.I32)
        True
        >>> is_string_convertible(BuiltinType.BOOL)
        True
        >>> is_string_convertible(BuiltinType.STRING)
        True
        >>> is_string_convertible(BuiltinType.BLANK)
        False
    """
    if isinstance(ty, BuiltinType):
        return ty in BUILTIN_STRING_CONVERTIBLE_TYPES
    # Arrays, structs, enums, iterators are not supported
    return False


def is_abstract_type(ty: Type, struct_table: Optional[dict] = None,
                     enum_table: Optional[dict] = None,
                     _visited: Optional[Set[str]] = None) -> bool:
    """Whether a type still mentions an unbound type parameter.

    An abstract type is a template artifact, not a real type: it exists only while a generic
    body is analysed with its own `<T, U>` still unbound. It must never be monomorphized and
    must never be interned into the enum table -- `Result<Either<U, T>, StdError>` would sit
    there depending on an `Either<U, T>` that is never itself interned, which strands the
    enum topological sort and gets misreported as a recursive enum (CE2052).

    A type parameter is not always spelled `TypeParameter`. By the time a generic function's
    signature reaches the monomorphizer its params survive as bare `UnknownType("U")` -- a name
    that resolves to no struct and no enum. That is what makes the tables load-bearing here:
    WITHOUT them an `UnknownType` cannot be told apart from a not-yet-resolved user type, so it
    is conservatively treated as concrete.

    Args:
        ty: The type to inspect (None-safe).
        struct_table: Optional name -> StructType mapping, to tell a user type from a type param.
        enum_table: Optional name -> EnumType mapping, likewise.
        _visited: Internal cycle guard over struct/enum names.

    Returns:
        True if the type transitively mentions an unbound type parameter.
    """
    from sushi_lang.semantics.typesys import (
        ArrayType, DynamicArrayType, ReferenceType, PointerType,
        IteratorType, StructType, EnumType, UnknownType,
    )
    from sushi_lang.semantics.generics.types import TypeParameter, GenericTypeRef

    if ty is None:
        return False
    if _visited is None:
        _visited = set()

    def recurse(inner: Type) -> bool:
        return is_abstract_type(inner, struct_table, enum_table, _visited)

    if isinstance(ty, TypeParameter):
        return True
    if isinstance(ty, UnknownType):
        if struct_table is None and enum_table is None:
            return False
        known = (struct_table or {}), (enum_table or {})
        return ty.name not in known[0] and ty.name not in known[1]
    if isinstance(ty, GenericTypeRef):
        return any(recurse(arg) for arg in (ty.type_args or ()))
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return recurse(ty.base_type)
    if isinstance(ty, ReferenceType):
        return recurse(ty.referenced_type)
    if isinstance(ty, PointerType):
        return recurse(ty.pointee_type)
    if isinstance(ty, IteratorType):
        return recurse(ty.element_type)
    if isinstance(ty, (StructType, EnumType)):
        # A monomorphized instance carries the args it was built from; an abstract one carries
        # the enclosing template's own parameters (Either<U, T>). `generic_args` is not enough:
        # it is None on anything not built by the monomorphizer, so the payloads themselves are
        # scanned too -- an abstract `Either<U, T>` has variants Left(U) / Right(T) whose
        # associated types ARE the bare type parameters.
        if ty.name in _visited:
            return False
        _visited.add(ty.name)
        if any(recurse(arg) for arg in (ty.generic_args or ())):
            return True
        if isinstance(ty, EnumType):
            return any(
                recurse(assoc)
                for variant in ty.variants
                for assoc in variant.associated_types
            )
        return any(recurse(field_type) for _, field_type in ty.fields)
    return False


def contains_foreign_ptr(ty: Type, struct_table: Optional[dict] = None,
                         enum_table: Optional[dict] = None,
                         _visited: Optional[Set[str]] = None) -> bool:
    """Recursively check whether a type exposes a foreign `ptr` (ForeignPtrType).

    Single source of truth for the ptr-exposure walk, shared by the CE5008 unit
    fence (Pass 2) and the CE5002 .slib manifest check. Beyond the concrete type
    shapes it also handles the Pass 2-era representations:
    - GenericTypeRef: walks type arguments (a `Result<ptr, E>` return is still a
      GenericTypeRef at validation time, before monomorphization rewrites it).
    - UnknownType: resolved through the optional struct/enum tables (dicts keyed
      by name); unresolvable names are treated as ptr-free, because struct fields
      are deliberately allowed to carry `ptr` across units (the wrapper-struct
      pattern) and the concrete shapes are checked where they are used.

    Args:
        ty: The type to inspect (None-safe).
        struct_table: Optional name -> StructType mapping for UnknownType resolution.
        enum_table: Optional name -> EnumType mapping for UnknownType resolution.
        _visited: Internal cycle guard over struct/enum names.

    Returns:
        True if the type transitively contains a ForeignPtrType.
    """
    from sushi_lang.semantics.typesys import (
        ForeignPtrType, ArrayType, DynamicArrayType, ReferenceType,
        PointerType, IteratorType, StructType, EnumType, UnknownType,
    )
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if ty is None:
        return False
    if _visited is None:
        _visited = set()

    def recurse(inner: Type) -> bool:
        return contains_foreign_ptr(inner, struct_table, enum_table, _visited)

    if isinstance(ty, ForeignPtrType):
        return True
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return recurse(ty.base_type)
    if isinstance(ty, ReferenceType):
        return recurse(ty.referenced_type)
    if isinstance(ty, PointerType):
        return recurse(ty.pointee_type)
    if isinstance(ty, IteratorType):
        return recurse(ty.element_type)
    if isinstance(ty, GenericTypeRef):
        return any(recurse(arg) for arg in (ty.type_args or ()))
    if isinstance(ty, UnknownType):
        resolved = None
        if struct_table and ty.name in struct_table:
            resolved = struct_table[ty.name]
        elif enum_table and ty.name in enum_table:
            resolved = enum_table[ty.name]
        return recurse(resolved) if resolved is not None else False
    if isinstance(ty, StructType):
        if ty.name in _visited:
            return False
        _visited.add(ty.name)
        return any(recurse(ft) for _, ft in ty.fields)
    if isinstance(ty, EnumType):
        if ty.name in _visited:
            return False
        _visited.add(ty.name)
        return any(
            recurse(at)
            for v in ty.variants for at in v.associated_types
        )
    return False
