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

from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.semantics.typesys import Type, EnumType, EnumVariantInfo, StructType
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    pass


class MonomorphizationDepthExceeded(Exception):
    """Raised when a generic type nests without bound during monomorphization.

    Tie-the-knot (see monomorphize_enum/monomorphize_struct) terminates a *finite*
    self-reference through an opaque pointer, but an ever-growing type argument
    (each self-reference adds a level) has no fixpoint. This sentinel unwinds the
    recursive substitution cleanly after CE0122 is reported, instead of surfacing a
    raw Python RecursionError.
    """


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

        from sushi_lang.semantics.type_predicates import is_abstract_type

        for base_name, type_args in instantiations:
            # Look up the generic enum definition
            if base_name not in generic_enums:
                # This shouldn't happen if type validation passes, but be defensive
                continue

            # An abstract instantiation still names an enclosing template's own type params
            # (`Result<Either<U, T>, StdError>`, collected off a generic fn's signature). There
            # is nothing to monomorphize until a concrete call site binds them, and producing a
            # bogus concrete enum strands the enum topological sort on an `Either<U, T>` that is
            # never itself interned -- which then gets misreported as a recursive enum (CE2052).
            # The concrete instantiations are collected separately.
            structs = self.monomorphizer.struct_table.by_name if self.monomorphizer.struct_table else {}
            enums = self.monomorphizer.enum_table.by_name if self.monomorphizer.enum_table else {}
            if any(is_abstract_type(arg, structs, enums) for arg in type_args):
                continue

            generic = generic_enums[base_name]

            # Generate concrete enum type
            try:
                concrete = self.monomorphize_enum(generic, type_args)
            except MonomorphizationDepthExceeded:
                # CE0122 already reported; the instantiation is infinitely
                # recursive. Skip it and let the reporter's error abort the build.
                continue

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
                name=f"{generic.name}@({', '.join(display_type(t) for t in type_args)})"
            )
            # Return a dummy enum to continue compilation
            return EnumType(name=f"{generic.name}<error>", variants=())

        # Validate perk constraints on type arguments
        self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map: type parameter name → concrete type
        # Example: {"T": BuiltinType.I32}
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args, strict=False):
            substitution[param.name] = arg

        # Generate unique name for concrete type
        # Example: "Result<i32>", "Result<string>", "Result<MyStruct>"
        concrete_name = self._generate_concrete_name(generic.name, type_args)

        # Tie-the-knot: publish an empty shell into the cache BEFORE substituting
        # variant types. A self-referential field (e.g. `Node(Own<Tree<T>>)`)
        # re-enters monomorphize_enum with this same cache_key; it now resolves to
        # the shell by identity instead of recursing forever. The shell is patched
        # in place below, so every self-reference sees the final variants once
        # substitution completes. Sound because the recursion passes through an
        # opaque pointer (Own<T>) — the backend never needs the pointee's layout
        # to size the field.
        concrete = EnumType(
            name=concrete_name,
            variants=(),
            generic_base=generic.name,
            generic_args=type_args
        )
        self.monomorphizer.cache[cache_key] = concrete

        # Substitute type parameters in all variants
        with self.monomorphizer._monomorphize_depth_guard(generic.name):
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

        # Patch the shell in place (EnumType is a frozen dataclass).
        object.__setattr__(concrete, "variants", tuple(concrete_variants))

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
            try:
                concrete = self.monomorphize_struct(generic, type_args)
            except MonomorphizationDepthExceeded:
                # CE0122 already reported; skip the infinitely recursive type.
                continue

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
                name=f"{generic.name}@({', '.join(display_type(t) for t in type_args)})"
            )
            # Return a dummy struct to continue compilation
            return StructType(name=f"{generic.name}<error>", fields=())

        # Validate perk constraints on type arguments
        self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map: type parameter name → concrete type
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args, strict=False):
            substitution[param.name] = arg

        # Generate unique name for concrete type
        concrete_name = self._generate_concrete_name(generic.name, type_args)

        # Tie-the-knot: publish an empty shell before substituting fields so a
        # self-referential field resolves to this same object by identity rather
        # than recursing forever (see monomorphize_enum for the full rationale).
        concrete = StructType(
            name=concrete_name,
            fields=(),
            generic_base=generic.name,
            generic_args=type_args
        )
        self.monomorphizer.struct_cache[cache_key] = concrete

        # Substitute type parameters in all fields
        with self.monomorphizer._monomorphize_depth_guard(generic.name):
            concrete_fields = []
            for field_name, field_type in generic.fields:
                concrete_type = self.monomorphizer.substitutor.substitute_type(
                    field_type, substitution
                )
                concrete_fields.append((field_name, concrete_type))

        # Patch the shell in place (StructType is a frozen dataclass).
        object.__setattr__(concrete, "fields", tuple(concrete_fields))

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
