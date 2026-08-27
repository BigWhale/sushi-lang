"""Type checking predicates and type sets for semantic analysis."""

from typing import Optional, Set
from sushi_lang.semantics.typesys import Type, BuiltinType


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


def is_numeric_type(ty: Type) -> bool:
    """Check if a type is numeric (integer or floating-point)."""
    return ty in BUILTIN_NUMERIC_TYPES


def is_integer_type(ty: Type) -> bool:
    """Check if a type is an integer type (signed or unsigned)."""
    return ty in BUILTIN_INTEGER_TYPES


def is_float_type(ty: Type) -> bool:
    """Check if a type is a floating-point type."""
    return ty in BUILTIN_FLOAT_TYPES


def is_unsigned_int(ty: Optional[Type]) -> bool:
    """Check if a type is an unsigned integer type (u8/u16/u32/u64)."""
    return ty in BUILTIN_UNSIGNED_INTEGER_TYPES


def is_string_convertible(ty: Type) -> bool:
    """Check if a type can be converted to string in string interpolation."""
    if isinstance(ty, BuiltinType):
        return ty in BUILTIN_STRING_CONVERTIBLE_TYPES
    return False


def is_abstract_type(ty: Type, struct_table: Optional[dict] = None,
                     enum_table: Optional[dict] = None,
                     _visited: Optional[Set[str]] = None) -> bool:
    """Whether a type still mentions an unbound type parameter.

    Not built on `walk_named_types`: an `UnknownType` absent from the tables is ABSTRACT
    here and a leaf there, and this walk also reads `generic_args`.
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
                         enum_table: Optional[dict] = None) -> bool:
    """Recursively check whether a type exposes a foreign `ptr` (ForeignPtrType)."""
    from sushi_lang.semantics.type_walk import walk_named_types
    from sushi_lang.semantics.typesys import ForeignPtrType

    return any(
        isinstance(reached, ForeignPtrType)
        for reached in walk_named_types(ty, struct_table, enum_table)
    )


def contains_reference(ty: Optional[Type]) -> bool:
    """Does this declared type contain a `peek` / `poke` anywhere it is not supported?

    Not built on `walk_named_types`: a reference IS supported as a lambda parameter, so
    this walk skips `FunctionType.param_types` on purpose and the shared walk does not.
    """
    from sushi_lang.semantics.typesys import (
        ArrayType, DynamicArrayType, FunctionType, IteratorType, PointerType,
        ReferenceType,
    )
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if ty is None:
        return False
    if isinstance(ty, ReferenceType):
        return True
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return contains_reference(ty.base_type)
    if isinstance(ty, PointerType):
        return contains_reference(ty.pointee_type)
    if isinstance(ty, IteratorType):
        return contains_reference(ty.element_type)
    if isinstance(ty, GenericTypeRef):
        return any(contains_reference(arg) for arg in (ty.type_args or ()))
    if isinstance(ty, FunctionType):
        # Parameters are the ONE supported position; the return is not (see the docstring).
        return contains_reference(ty.ok_type) or contains_reference(ty.err_type)
    return False
