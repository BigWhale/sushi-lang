"""
Generic type definitions for Sushi Lang.

This module contains the infrastructure for generic types, including:
- TypeParameter: Represents generic type parameters (e.g., T, E)
- GenericEnumType: Generic enum definition with type parameters
- GenericTypeRef: Reference to a generic type with concrete type arguments
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from semantics.typesys import Type, EnumType, EnumVariantInfo
    from semantics.ast import BoundedTypeParam

# Type alias for type parameters (supports both old and new formats)
TypeParam = Union['TypeParameter', 'BoundedTypeParam']


@dataclass(frozen=True)
class TypeParameter:
    """Represents a generic type parameter.

    Example: In `enum Result<T, E>:`, both T and E are TypeParameters.

    Type parameters act as placeholders that get substituted with concrete
    types during monomorphization.
    """
    name: str  # Parameter name (e.g., "T", "E", "U")

    def __str__(self) -> str:
        return self.name

    def __hash__(self) -> int:
        return hash(("type_param", self.name))

    def __eq__(self, other) -> bool:
        return isinstance(other, TypeParameter) and self.name == other.name


@dataclass(frozen=True)
class GenericEnumType:
    """A generic enum definition with type parameters.

    Example: `enum Result<T>:` where T is a type parameter.

    This is the generic "template" that gets instantiated into concrete
    EnumType instances during monomorphization. For example:
    - Result<T> (generic) + i32 → Result<i32> (concrete EnumType)
    - Result<T> (generic) + MyStruct → Result<MyStruct> (concrete EnumType)

    The variants can reference type parameters in their associated types.
    For example, Result<T> has:
    - Ok variant with associated type T
    - Err variant with no associated types
    """
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
        """Create a concrete EnumType by substituting type parameters.

        Args:
            type_args: Concrete types to substitute for type parameters
                      (must match length of self.type_params)

        Returns:
            A concrete EnumType with all type parameters replaced

        Example:
            generic = GenericEnumType("Result", ("T",), [Ok(T), Err()])
            concrete = generic.instantiate((BuiltinType.I32,))
            # Returns: EnumType("Result<i32>", [Ok(i32), Err()])
        """
        from semantics.typesys import EnumType, EnumVariantInfo

        if len(type_args) != len(self.type_params):
            raise ValueError(
                f"Type argument count mismatch: expected {len(self.type_params)}, "
                f"got {len(type_args)}"
            )

        # Build substitution mapping: param_name -> concrete_type
        substitution = {}
        for param, arg in zip(self.type_params, type_args):
            substitution[param.name] = arg

        # Substitute type parameters in all variant associated types
        concrete_variants = []
        for variant in self.variants:
            concrete_associated_types = []
            for assoc_type in variant.associated_types:
                # If it's a TypeParameter, substitute it
                if isinstance(assoc_type, TypeParameter):
                    if assoc_type.name in substitution:
                        concrete_associated_types.append(substitution[assoc_type.name])
                    else:
                        # This shouldn't happen if the generic enum is well-formed
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

        # Create concrete enum name with type arguments
        type_arg_strs = ", ".join(str(t) for t in type_args)
        concrete_name = f"{self.name}<{type_arg_strs}>"

        return EnumType(
            name=concrete_name,
            variants=tuple(concrete_variants)
        )


@dataclass(frozen=True)
class GenericStructType:
    """A generic struct definition with type parameters.

    Example: `struct Pair<T, U>:` where T and U are type parameters.

    This is the generic "template" that gets instantiated into concrete
    StructType instances during monomorphization. For example:
    - Pair<T, U> (generic) + (i32, string) → Pair<i32, string> (concrete StructType)
    - Box<T> (generic) + (i32,) → Box<i32> (concrete StructType)

    The fields can reference type parameters in their types.
    For example, Pair<T, U> has:
    - first field with type T
    - second field with type U
    """
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
        """Create a concrete StructType by substituting type parameters.

        Args:
            type_args: Concrete types to substitute for type parameters
                      (must match length of self.type_params)

        Returns:
            A concrete StructType with all type parameters replaced

        Example:
            generic = GenericStructType("Pair", (TypeParameter("T"), TypeParameter("U")),
                                       (("first", TypeParameter("T")), ("second", TypeParameter("U"))))
            concrete = generic.instantiate((BuiltinType.I32, BuiltinType.STRING))
            # Returns: StructType("Pair<i32, string>", (("first", i32), ("second", string)))
        """
        from semantics.typesys import StructType

        if len(type_args) != len(self.type_params):
            raise ValueError(
                f"Type argument count mismatch: expected {len(self.type_params)}, "
                f"got {len(type_args)}"
            )

        # Build substitution mapping: param_name -> concrete_type
        substitution = {}
        for param, arg in zip(self.type_params, type_args):
            substitution[param.name] = arg

        # Substitute type parameters in all field types
        concrete_fields = []
        for field_name, field_type in self.fields:
            # Recursively substitute type parameters in field types
            concrete_type = _substitute_type_params(field_type, substitution)
            concrete_fields.append((field_name, concrete_type))

        # Create concrete struct name with type arguments
        type_arg_strs = ", ".join(str(t) for t in type_args)
        concrete_name = f"{self.name}<{type_arg_strs}>"

        return StructType(
            name=concrete_name,
            fields=tuple(concrete_fields)
        )


@dataclass(frozen=True)
class GenericTypeRef:
    """Reference to a generic type with concrete type arguments.

    Example: `Result<i32>` references the generic type "Result" with
    the concrete type argument i32.

    This is used in type annotations before monomorphization. During
    semantic analysis, GenericTypeRef nodes are resolved to concrete
    EnumType or StructType instances.

    The base_name refers to a GenericEnumType or GenericStructType that
    must be registered in the type environment.
    """
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


def _substitute_type_params(ty: Type, substitution: dict[str, Type]) -> Type:
    """Recursively substitute type parameters in a type.

    Handles nested types like PointerType(TypeParameter), ArrayType(TypeParameter), etc.

    Args:
        ty: The type to substitute in
        substitution: Mapping from type parameter names to concrete types

    Returns:
        Type with all type parameters substituted
    """
    from semantics.typesys import PointerType, ArrayType, DynamicArrayType, ReferenceType

    # Direct type parameter
    if isinstance(ty, TypeParameter):
        if ty.name in substitution:
            return substitution[ty.name]
        else:
            raise ValueError(f"Unknown type parameter: {ty.name}")

    # Pointer type: substitute in pointee
    elif isinstance(ty, PointerType):
        substituted_pointee = _substitute_type_params(ty.pointee_type, substitution)
        return PointerType(pointee_type=substituted_pointee)

    # Array type: substitute in element type
    elif isinstance(ty, ArrayType):
        substituted_base = _substitute_type_params(ty.base_type, substitution)
        return ArrayType(base_type=substituted_base, size=ty.size)

    # Dynamic array type: substitute in element type
    elif isinstance(ty, DynamicArrayType):
        substituted_base = _substitute_type_params(ty.base_type, substitution)
        return DynamicArrayType(base_type=substituted_base)

    # Reference type: substitute in referenced type (preserve mutability)
    elif isinstance(ty, ReferenceType):
        substituted_ref = _substitute_type_params(ty.referenced_type, substitution)
        return ReferenceType(referenced_type=substituted_ref, mutability=ty.mutability)

    # No substitution needed for other types
    else:
        return ty


# Update the Type union to include generic types
# This will be imported in typesys.py
__all__ = ["TypeParameter", "GenericEnumType", "GenericStructType", "GenericTypeRef"]
