"""Enum and struct type monomorphization."""
from __future__ import annotations
from typing import Dict, Tuple, Set, TYPE_CHECKING

from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.semantics.typesys import Type, EnumType, EnumVariantInfo, StructType
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    pass


class MonomorphizationDepthExceeded(Exception):
    """Raised when a generic type nests without bound during monomorphization."""


class TypeMonomorphizer:
    """Handles monomorphization of generic enum and struct types."""

    def __init__(self, monomorphizer):
        """Initialize type monomorphizer."""
        self.monomorphizer = monomorphizer

    def monomorphize_all_enums(
        self,
        generic_enums: Dict[str, GenericEnumType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, EnumType]:
        """Monomorphize all collected generic enum instantiations."""
        self.monomorphizer.generic_enums = generic_enums

        concrete_enums: Dict[str, EnumType] = {}

        from sushi_lang.semantics.type_predicates import is_abstract_type

        for base_name, type_args in instantiations:
            if base_name not in generic_enums:
                continue

            # An abstract instantiation still names an enclosing template's type params, so
            # there is nothing to monomorphize until a call site binds them. A bogus concrete
            # enum strands the topological sort on a type never interned, misreported as a
            # recursive enum (CE2052).
            structs = self.monomorphizer.struct_table.by_name if self.monomorphizer.struct_table else {}
            enums = self.monomorphizer.enum_table.by_name if self.monomorphizer.enum_table else {}
            if any(is_abstract_type(arg, structs, enums) for arg in type_args):
                continue

            generic = generic_enums[base_name]

            try:
                concrete = self.monomorphize_enum(generic, type_args)
            except MonomorphizationDepthExceeded:
                # CE0122 already reported; the instantiation is infinitely
                # recursive. Skip it and let the reporter's error abort the build.
                continue

            concrete_enums[concrete.name] = concrete

        return concrete_enums

    def monomorphize_enum(
        self,
        generic: GenericEnumType,
        type_args: Tuple[Type, ...]
    ) -> EnumType:
        """Create concrete enum by substituting type parameters."""
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.cache:
            return self.monomorphizer.cache[cache_key]

        if len(type_args) != len(generic.type_params):
            er.emit(
                self.monomorphizer.reporter,
                er.ERR.CE2001,  # Use generic type error for now
                None,
                name=f"{generic.name}@({', '.join(display_type(t) for t in type_args)})"
            )
            return EnumType(name=f"{generic.name}<error>", variants=())

        self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args, strict=False):
            substitution[param.name] = arg

        concrete_name = self._generate_concrete_name(generic.name, type_args)

        # Tie-the-knot: publish an empty shell BEFORE substituting variant types, so a
        # self-referential field re-entering with this cache_key resolves by identity
        # instead of recursing forever. The shell is patched IN PLACE below. Sound because
        # the recursion passes through an opaque `Own<T>` pointer.
        concrete = EnumType(
            name=concrete_name,
            variants=(),
            generic_base=generic.name,
            generic_args=type_args
        )
        self.monomorphizer.cache[cache_key] = concrete

        with self.monomorphizer._monomorphize_depth_guard(generic.name):
            concrete_variants = []
            for variant in generic.variants:
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

        object.__setattr__(concrete, "variants", tuple(concrete_variants))

        return concrete

    def monomorphize_all_structs(
        self,
        generic_structs: Dict[str, GenericStructType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, StructType]:
        """Monomorphize all collected generic struct instantiations."""
        self.monomorphizer.generic_structs = generic_structs

        concrete_structs: Dict[str, StructType] = {}

        for base_name, type_args in instantiations:
            if base_name not in generic_structs:
                continue

            generic = generic_structs[base_name]

            try:
                concrete = self.monomorphize_struct(generic, type_args)
            except MonomorphizationDepthExceeded:
                # CE0122 already reported; skip the infinitely recursive type.
                continue

            concrete_structs[concrete.name] = concrete

        return concrete_structs

    def monomorphize_struct(
        self,
        generic: GenericStructType,
        type_args: Tuple[Type, ...]
    ) -> StructType:
        """Create concrete struct by substituting type parameters."""
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.struct_cache:
            return self.monomorphizer.struct_cache[cache_key]

        if len(type_args) != len(generic.type_params):
            er.emit(
                self.monomorphizer.reporter,
                er.ERR.CE2001,  # Use generic type error for now
                None,
                name=f"{generic.name}@({', '.join(display_type(t) for t in type_args)})"
            )
            return StructType(name=f"{generic.name}<error>", fields=())

        self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args, strict=False):
            substitution[param.name] = arg

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

        with self.monomorphizer._monomorphize_depth_guard(generic.name):
            concrete_fields = []
            for field_name, field_type in generic.fields:
                concrete_type = self.monomorphizer.substitutor.substitute_type(
                    field_type, substitution
                )
                concrete_fields.append((field_name, concrete_type))

        object.__setattr__(concrete, "fields", tuple(concrete_fields))

        return concrete

    def _generate_concrete_name(self, base_name: str, type_args: Tuple[Type, ...]) -> str:
        """Generate a unique name for a concrete generic type."""
        if not type_args:
            return base_name

        arg_strs = [self._type_to_string(arg) for arg in type_args]

        return f"{base_name}<{', '.join(arg_strs)}>"

    def _type_to_string(self, ty: Type) -> str:
        """Convert a type to its string representation for name generation."""
        return str(ty)
