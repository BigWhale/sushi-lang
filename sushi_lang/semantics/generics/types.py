"""Generic type definitions for Sushi Lang."""

from __future__ import annotations
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Tuple, Union

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type, EnumType, EnumVariantInfo, StructType
    from sushi_lang.semantics.ast import BoundedTypeParam

TypeParam = Union['TypeParameter', 'BoundedTypeParam']


@dataclass(frozen=True)
class TypeParameter:
    """Represents a generic type parameter."""
    name: str  # Parameter name (e.g., "T", "E", "U")

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
        params = ", ".join(str(tp) for tp in self.type_params)
        return f"{self.name}<{params}>"

    def __hash__(self) -> int:
        return hash(("generic_enum", self.name, self.type_params))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericEnumType) and
                self.name == other.name and
                self.type_params == other.type_params and
                self.variants == other.variants)

    def instantiate(self, type_args: tuple[Type, ...]) -> EnumType:
        """Create a concrete EnumType by substituting type parameters."""
        from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo

        if len(type_args) != len(self.type_params):
            raise ValueError(
                f"Type argument count mismatch: expected {len(self.type_params)}, "
                f"got {len(type_args)}"
            )

        substitution = {}
        for param, arg in zip(self.type_params, type_args, strict=False):
            substitution[param.name] = arg

        concrete_variants = []
        for variant in self.variants:
            concrete_associated_types = []
            for assoc_type in variant.associated_types:
                if isinstance(assoc_type, TypeParameter):
                    if assoc_type.name in substitution:
                        concrete_associated_types.append(substitution[assoc_type.name])
                    else:
                        raise ValueError(f"Unknown type parameter: {assoc_type.name}")
                else:
                    # Not a type parameter (could be concrete type or nested generic).
                    # Nested generics are handled by Monomorphizer._substitute_type()
                    # during Pass 1.6, so we just pass this through unchanged.
                    concrete_associated_types.append(assoc_type)

            concrete_variants.append(EnumVariantInfo(
                name=variant.name,
                associated_types=tuple(concrete_associated_types)
            ))

        type_arg_strs = ", ".join(str(t) for t in type_args)
        concrete_name = f"{self.name}<{type_arg_strs}>"

        return EnumType(
            name=concrete_name,
            variants=tuple(concrete_variants)
        )


@dataclass(frozen=True)
class GenericStructType:
    """A generic struct definition with type parameters."""
    name: str                                    # Generic struct name (e.g., "Pair", "Box")
    type_params: tuple[TypeParam, ...]           # Type parameters (TypeParameter or BoundedTypeParam)
    fields: tuple[tuple[str, Type], ...]         # Fields (may contain TypeParameters in field types)

    def __str__(self) -> str:
        params = ", ".join(str(tp) for tp in self.type_params)
        return f"{self.name}<{params}>"

    def __hash__(self) -> int:
        return hash(("generic_struct", self.name, self.type_params))

    def __eq__(self, other) -> bool:
        return (isinstance(other, GenericStructType) and
                self.name == other.name and
                self.type_params == other.type_params and
                self.fields == other.fields)

    def instantiate(self, type_args: tuple[Type, ...]) -> StructType:
        """Create a concrete StructType by substituting type parameters."""
        from sushi_lang.semantics.typesys import StructType

        if len(type_args) != len(self.type_params):
            raise ValueError(
                f"Type argument count mismatch: expected {len(self.type_params)}, "
                f"got {len(type_args)}"
            )

        substitution = {}
        for param, arg in zip(self.type_params, type_args, strict=False):
            substitution[param.name] = arg

        concrete_fields = []
        for field_name, field_type in self.fields:
            concrete_type = substitute_type_params(field_type, substitution)
            concrete_fields.append((field_name, concrete_type))

        type_arg_strs = ", ".join(str(t) for t in type_args)
        concrete_name = f"{self.name}<{type_arg_strs}>"

        return StructType(
            name=concrete_name,
            fields=tuple(concrete_fields)
        )


@dataclass(frozen=True)
class GenericTypeRef:
    """Reference to a generic type with concrete type arguments."""
    base_name: str                    # Generic type name (e.g., "Result", "Option", "Pair", "Box")
    type_args: tuple[Type, ...]       # Concrete type arguments (e.g., (BuiltinType.I32,))

    def __str__(self) -> str:
        args = ", ".join(str(t) for t in self.type_args)
        return f"{self.base_name}<{args}>"

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
    demand, and is what Pass 1.6 uses.

    An unbound name passes through, rather than raising: a signature may legitimately name
    a type parameter this substitution does not bind.

    A NAMED type (StructType/EnumType) is terminal: its interned name already IS
    (declaration, type arguments), so there is nothing left to substitute (#240).

    **It does NOT substitute an `UnknownType`, and that is deliberate.** A type parameter
    written inside a generic type reaches a declared position spelled as an UnknownType
    (the collect pass converts only a TOP-LEVEL one to a `TypeParameter`), so adding that
    arm is what would let `extend Box@(T) f() Maybe@(T)` monomorphize its signature -- and
    every instantiation of a generic-target extension SHARES one body AST, so the second
    instantiation of such a method emits a function whose stamps belong to the first.
    The arm belongs with the per-instantiation body work, not ahead of it (#389, #390).
    """
    from sushi_lang.semantics.typesys import (PointerType, ArrayType, DynamicArrayType,
                                              ReferenceType, FunctionType)

    if isinstance(ty, TypeParameter):
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


__all__ = ["TypeParameter", "TypePack", "GenericEnumType", "GenericStructType",
           "GenericTypeRef", "substitute_type_params"]
