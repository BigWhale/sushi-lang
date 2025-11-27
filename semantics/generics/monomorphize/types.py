# semantics/generics/monomorphize/types.py
"""
Enum and struct type monomorphization.

This module handles the monomorphization of generic enum and struct types,
converting generic type definitions with type parameters into concrete types
with specific type arguments.

Example:
    GenericEnumType("Result", ["T"]) + [i32] → EnumType("Result<i32>")
    GenericStructType("Pair", ["T", "U"]) + [i32, string] → StructType("Pair<i32, string>")
"""
from __future__ import annotations
from typing import Dict, Tuple, Set, TYPE_CHECKING

from semantics.generics.types import GenericEnumType, GenericStructType
from semantics.typesys import Type, EnumType, EnumVariantInfo, StructType
from internals import errors as er

if TYPE_CHECKING:
    from internals.report import Reporter


class TypeMonomorphizer:
    """Handles monomorphization of generic enum and struct types.

    This class is responsible for:
    - Converting GenericEnumType + type args → concrete EnumType
    - Converting GenericStructType + type args → concrete StructType
    - Caching monomorphized types to avoid duplication
    - Recursively monomorphizing nested generic types
    """

    def __init__(self, monomorphizer):
        """Initialize type monomorphizer.

        Args:
            monomorphizer: Parent Monomorphizer instance (provides access to
                           caches, generic tables, and substitutor)
        """
        self.monomorphizer = monomorphizer

    def monomorphize_all_enums(
        self,
        generic_enums: Dict[str, GenericEnumType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, EnumType]:
        """Monomorphize all collected generic enum instantiations.

        Args:
            generic_enums: Table of GenericEnumType definitions (from CollectorPass)
            instantiations: Set of (base_name, type_args) tuples (from InstantiationCollector)

        Returns:
            Dictionary mapping concrete type names (e.g., "Result<i32>") to EnumType instances
        """
        # Store generic enums for recursive monomorphization
        self.monomorphizer.generic_enums = generic_enums

        concrete_enums: Dict[str, EnumType] = {}

        for base_name, type_args in instantiations:
            # Look up the generic enum definition
            if base_name not in generic_enums:
                # This shouldn't happen if type validation passes, but be defensive
                continue

            generic = generic_enums[base_name]

            # Generate concrete enum type
            concrete = self.monomorphize_enum(generic, type_args)

            # Store by concrete name (e.g., "Result<i32>")
            concrete_enums[concrete.name] = concrete

        return concrete_enums

    def monomorphize_enum(
        self,
        generic: GenericEnumType,
        type_args: Tuple[Type, ...]
    ) -> EnumType:
        """Create concrete enum by substituting type parameters.

        Args:
            generic: The generic enum definition (e.g., Result<T>)
            type_args: Concrete type arguments (e.g., (BuiltinType.I32,))

        Returns:
            Concrete EnumType with substituted types
        """
        # Check cache first
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.cache:
            return self.monomorphizer.cache[cache_key]

        # Validate that number of type arguments matches number of type parameters
        if len(type_args) != len(generic.type_params):
            # This shouldn't happen if type validation passes, but emit error to be safe
            er.emit(
                self.monomorphizer.reporter,
                er.ERR.CE2001,  # Use generic type error for now
                None,
                name=f"{generic.name}<{', '.join(str(t) for t in type_args)}>"
            )
            # Return a dummy enum to continue compilation
            return EnumType(name=f"{generic.name}<error>", variants=())

        # Validate perk constraints on type arguments
        self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map: type parameter name → concrete type
        # Example: {"T": BuiltinType.I32}
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args):
            substitution[param.name] = arg

        # Substitute type parameters in all variants
        concrete_variants = []
        for variant in generic.variants:
            # Substitute in associated types
            concrete_associated_types = []
            for assoc_type in variant.associated_types:
                concrete_type = self.monomorphizer.substitutor.substitute_type(
                    assoc_type, substitution
                )
                concrete_associated_types.append(concrete_type)

            concrete_variants.append(EnumVariantInfo(
                name=variant.name,
                associated_types=tuple(concrete_associated_types)
            ))

        # Generate unique name for concrete type
        # Example: "Result<i32>", "Result<string>", "Result<MyStruct>"
        concrete_name = self._generate_concrete_name(generic.name, type_args)

        # Create concrete EnumType with generic metadata
        concrete = EnumType(
            name=concrete_name,
            variants=tuple(concrete_variants),
            generic_base=generic.name,
            generic_args=type_args
        )

        # Cache the result
        self.monomorphizer.cache[cache_key] = concrete

        return concrete

    def monomorphize_all_structs(
        self,
        generic_structs: Dict[str, GenericStructType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, StructType]:
        """Monomorphize all collected generic struct instantiations.

        Args:
            generic_structs: Table of GenericStructType definitions (from CollectorPass)
            instantiations: Set of (base_name, type_args) tuples (from InstantiationCollector)

        Returns:
            Dictionary mapping concrete type names (e.g., "Pair<i32, string>") to StructType instances
        """
        # Store generic structs for recursive monomorphization
        self.monomorphizer.generic_structs = generic_structs

        concrete_structs: Dict[str, StructType] = {}

        for base_name, type_args in instantiations:
            # Look up the generic struct definition
            if base_name not in generic_structs:
                # This shouldn't happen if type validation passes, but be defensive
                continue

            generic = generic_structs[base_name]

            # Generate concrete struct type
            concrete = self.monomorphize_struct(generic, type_args)

            # Store by concrete name (e.g., "Pair<i32, string>")
            concrete_structs[concrete.name] = concrete

        return concrete_structs

    def monomorphize_struct(
        self,
        generic: GenericStructType,
        type_args: Tuple[Type, ...]
    ) -> StructType:
        """Create concrete struct by substituting type parameters.

        Args:
            generic: The generic struct definition (e.g., Pair<T, U>)
            type_args: Concrete type arguments (e.g., (BuiltinType.I32, BuiltinType.STRING))

        Returns:
            Concrete StructType with substituted types
        """
        # Check cache first
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.struct_cache:
            return self.monomorphizer.struct_cache[cache_key]

        # Validate that number of type arguments matches number of type parameters
        if len(type_args) != len(generic.type_params):
            # This shouldn't happen if type validation passes, but emit error to be safe
            er.emit(
                self.monomorphizer.reporter,
                er.ERR.CE2001,  # Use generic type error for now
                None,
                name=f"{generic.name}<{', '.join(str(t) for t in type_args)}>"
            )
            # Return a dummy struct to continue compilation
            return StructType(name=f"{generic.name}<error>", fields=())

        # Validate perk constraints on type arguments
        self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map: type parameter name → concrete type
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args):
            substitution[param.name] = arg

        # Substitute type parameters in all fields
        concrete_fields = []
        for field_name, field_type in generic.fields:
            concrete_type = self.monomorphizer.substitutor.substitute_type(
                field_type, substitution
            )
            concrete_fields.append((field_name, concrete_type))

        # Generate unique name for concrete type
        concrete_name = self._generate_concrete_name(generic.name, type_args)

        # Create concrete StructType with generic metadata
        concrete = StructType(
            name=concrete_name,
            fields=tuple(concrete_fields),
            generic_base=generic.name,
            generic_args=type_args
        )

        # Cache the result
        self.monomorphizer.struct_cache[cache_key] = concrete

        return concrete

    def _generate_concrete_name(self, base_name: str, type_args: Tuple[Type, ...]) -> str:
        """Generate a unique name for a concrete generic type.

        Args:
            base_name: Base generic name (e.g., "Result")
            type_args: Concrete type arguments (e.g., (BuiltinType.I32,))

        Returns:
            Concrete type name (e.g., "Result<i32>")
        """
        if not type_args:
            return base_name

        # Format type arguments as strings
        arg_strs = [self._type_to_string(arg) for arg in type_args]

        # Build concrete name: Result<i32>, Result<string>, etc.
        return f"{base_name}<{', '.join(arg_strs)}>"

    def _type_to_string(self, ty: Type) -> str:
        """Convert a type to its string representation for name generation.

        Args:
            ty: The type to convert

        Returns:
            String representation of the type
        """
        # Use the built-in str() which should work for all Type instances
        # BuiltinType, ArrayType, etc. all have __str__ implementations
        return str(ty)
