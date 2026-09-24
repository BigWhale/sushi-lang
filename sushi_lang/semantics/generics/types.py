"""Generic type definitions for Sushi Lang."""

from __future__ import annotations
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Optional, Tuple, Union

from sushi_lang.semantics.generics.interned import interned_name

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type, EnumVariantInfo
    from sushi_lang.semantics.ast import BoundedTypeParam

TypeParam = Union['TypeParameter', 'BoundedTypeParam']


@dataclass(frozen=True)
class TypeParameter:
    """Represents a generic type parameter."""
    name: str  # Parameter name (e.g., "T", "E", "U")
    # A bare parameter is never a pack; `BoundedTypeParam` carries the field that can be.
    is_pack: ClassVar[bool] = False

    def __str__(self) -> str:
        return self.name

    def __hash__(self) -> int:
        return hash(("type_param", self.name))

    def __eq__(self, other) -> bool:
        return isinstance(other, TypeParameter) and self.name == other.name


@dataclass(frozen=True)
class TypePack:
    """A type-parameter bound to an ordered, variable-length pack of types."""
    types: Tuple[Type, ...]  # Ordered concrete types absorbed by the pack (zero or more)

    def __str__(self) -> str:
        inner = ", ".join(str(t) for t in self.types)
        return f"pack({inner})"


@dataclass(frozen=True)
class GenericEnumType:
    """A generic enum definition with type parameters."""
    name: str                                    # Generic enum name (e.g., "Result")
    type_params: tuple[TypeParam, ...]           # Type parameters (TypeParameter or BoundedTypeParam)
    variants: tuple[EnumVariantInfo, ...]        # Variants (may contain TypeParameters in associated types)

    def __str__(self) -> str:
        return interned_name(self.name, self.type_params)

    def __hash__(self) -> int:
        return hash(("generic_enum", self.name, self.type_params))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericEnumType) and
                self.name == other.name and
                self.type_params == other.type_params and
                self.variants == other.variants)


@dataclass(frozen=True)
class GenericStructType:
    """A generic struct definition with type parameters."""
    name: str                                    # Generic struct name (e.g., "Pair", "Box")
    type_params: tuple[TypeParam, ...]           # Type parameters (TypeParameter or BoundedTypeParam)
    fields: tuple[tuple[str, Type], ...]         # Fields (may contain TypeParameters in field types)

    def __str__(self) -> str:
        return interned_name(self.name, self.type_params)

    def __hash__(self) -> int:
        return hash(("generic_struct", self.name, self.type_params))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericStructType) and
                self.name == other.name and
                self.type_params == other.type_params and
                self.fields == other.fields)


@dataclass(frozen=True)
class GenericTypeRef:
    """Reference to a generic type with concrete type arguments."""
    base_name: str                    # Generic type name (e.g., "Result", "Option", "Pair", "Box")
    type_args: tuple[Type, ...]       # Concrete type arguments (e.g., (BuiltinType.I32,))
    # The alias the base name was written behind. Identity ignores it, exactly as
    # `UnknownType.namespace` is ignored: the interned name is what `__str__` spells.
    namespace: Optional[str] = None

    def __str__(self) -> str:
        return interned_name(self.base_name, self.type_args)

    def __hash__(self) -> int:
        return hash(("generic_ref", self.base_name, self.type_args))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericTypeRef) and
                self.base_name == other.base_name and
                self.type_args == other.type_args)


def substitute_type_params(ty: Type, substitution: dict[str, Type]) -> Type:
    """Recursively put a type argument in the place of each type parameter it names.

    The PURE substitution: it rewrites a type and interns nothing, so a `Maybe@(T)` comes
    back as `Maybe@(i32)` rather than as the monomorphized `EnumType`. Its counterpart is
    `monomorphize.TypeSubstitutor.substitute_type`, which substitutes AND monomorphizes on
    demand, and is what the monomorphize pass uses.

    An unbound name passes through, rather than raising: a signature may legitimately name
    a type parameter this substitution does not bind.

    A NAMED type (StructType/EnumType) is terminal: its interned name already IS
    (declaration, type arguments), so there is nothing left to substitute (#240).

    Two arms carry #389, and each was missing from one of the two copies this replaces. A
    type parameter reaches a DECLARED position spelled as an **UnknownType** -- the collect
    pass converts only a TOP-LEVEL one to a `TypeParameter` -- and it is usually nested
    inside a **GenericTypeRef**, so `Maybe@(T)` in a generic-target extension's signature
    used to survive monomorphization untouched and report CE2001 on `T`.
    """
    from sushi_lang.semantics.typesys import (PointerType, ArrayType, DynamicArrayType,
                                              ReferenceType, FunctionType, UnknownType)

    if isinstance(ty, (TypeParameter, UnknownType)):
        return substitution.get(ty.name, ty)

    elif isinstance(ty, GenericTypeRef):
        return GenericTypeRef(
            base_name=ty.base_name,
            type_args=tuple(substitute_type_params(a, substitution) for a in ty.type_args),
        )

    elif isinstance(ty, PointerType):
        substituted_pointee = substitute_type_params(ty.pointee_type, substitution)
        return PointerType(pointee_type=substituted_pointee)

    elif isinstance(ty, ArrayType):
        substituted_base = substitute_type_params(ty.base_type, substitution)
        return ArrayType(base_type=substituted_base, size=ty.size)

    elif isinstance(ty, DynamicArrayType):
        substituted_base = substitute_type_params(ty.base_type, substitution)
        return DynamicArrayType(base_type=substituted_base)

    elif isinstance(ty, ReferenceType):
        substituted_ref = substitute_type_params(ty.referenced_type, substitution)
        return ReferenceType(referenced_type=substituted_ref, mutability=ty.mutability)

    elif isinstance(ty, FunctionType):
        # `replace`, so `param_modes` rides along beside `captures` (#368).
        return replace(
            ty,
            param_types=tuple(substitute_type_params(p, substitution) for p in ty.param_types),
            ok_type=substitute_type_params(ty.ok_type, substitution),
            err_type=substitute_type_params(ty.err_type, substitution),
        )

    else:
        return ty


def type_param_substitution(generic_func, type_args) -> Optional[dict[str, Type]]:
    """Type parameter name -> type argument. A pack fans out and binds nothing here."""
    params = [tp for tp in generic_func.type_params if not tp.is_pack]
    args = [arg for arg in type_args if not isinstance(arg, TypePack)]
    if len(params) != len(args):
        return None
    return {(tp.name if hasattr(tp, "name") else str(tp)): arg
            for tp, arg in zip(params, args, strict=True)}


def substituted_call_result(generic_func, type_args) -> Optional[Type]:
    """What a call to a generic declaration yields, read from its SUBSTITUTED signature.

    ONE derivation, because two passes ask the same question before the instance exists:
    the instantiate pass types a generic call that is a `match` scrutinee (#549), and the
    typecheck pass types one that is another generic call's argument (#556). Neither can
    go through the monomorphized copy -- it is not built yet -- so the template's return
    is substituted and wrapped exactly as the declaration wraps it: an explicit
    `Result@(T, E)` is its own two arms, anything else takes the spelled channel, and a
    signature with none takes `StdError`.

    The PURE substitution is used, so nothing is interned here. A payload whose instance
    does not exist yet comes back as a `GenericTypeRef`, which is what keeps an early
    answer out of the enum table.
    """
    from sushi_lang.semantics.typesys import UnknownType

    substitution = type_param_substitution(generic_func, type_args)
    if substitution is None or generic_func.ret is None:
        return None

    ret = substitute_type_params(generic_func.ret, substitution)
    if isinstance(ret, GenericTypeRef) and ret.base_name == "Result":
        return ret
    err = generic_func.err_type
    err = (substitute_type_params(err, substitution) if err is not None
           else UnknownType("StdError"))
    return GenericTypeRef(base_name="Result", type_args=(ret, err))


__all__ = ["TypeParameter", "TypePack", "GenericEnumType", "GenericStructType",
           "GenericTypeRef", "substitute_type_params", "type_param_substitution",
           "substituted_call_result"]
