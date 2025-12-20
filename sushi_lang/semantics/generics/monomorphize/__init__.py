# semantics/generics/monomorphize/__init__.py
"""
Pass 1.6: Monomorphization

Generates concrete EnumType, StructType, and FuncDef instances for each generic instantiation.
Takes GenericEnumType + type arguments → produces concrete EnumType.
Takes GenericStructType + type arguments → produces concrete StructType.
Takes GenericFuncDef + type arguments → produces concrete FuncDef.

Example:
    GenericEnumType("Result", ["T"]) + [i32] → EnumType("Result<i32>")
    GenericStructType("Pair", ["T", "U"]) + [i32, string] → StructType("Pair<i32, string>")
    GenericFuncDef("identity", ["T"]) + [i32] → FuncDef("identity__i32")

This module provides a facade that maintains the original Monomorphizer API
while delegating to specialized sub-modules.

Nested Generics Support:
The type substitution recursively handles nested generic types like
Result<Maybe<i32>> by:
1. Recursively substituting type arguments in GenericTypeRef nodes
2. Checking the cache for already-monomorphized nested generics
3. Recursively monomorphizing nested generics on-demand
This ensures zero-overhead monomorphization for arbitrarily nested generics.
"""
from __future__ import annotations
from typing import Dict, Tuple, Set
from dataclasses import dataclass, field

from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.semantics.typesys import Type, EnumType, StructType
from sushi_lang.internals.report import Reporter

# Import constraint validator for perk constraint checking
try:
    from sushi_lang.semantics.generics.constraints import ConstraintValidator
except ImportError:
    ConstraintValidator = None  # Graceful degradation if not available

# Import specialized sub-modules
from .transformer import TypeSubstitutor
from .types import TypeMonomorphizer
from .functions import FunctionMonomorphizer


@dataclass
class Monomorphizer:
    """Generates concrete enum types, struct types, and function definitions from generic definitions.

    This facade class maintains the original Monomorphizer API while delegating
    to specialized sub-modules:
    - TypeSubstitutor: Type parameter substitution and AST transformation
    - TypeMonomorphizer: Enum and struct monomorphization
    - FunctionMonomorphizer: Function monomorphization and nested instantiation detection

    For each generic type instantiation (e.g., Result<i32>), this class:
    1. Takes the GenericEnumType definition (Result<T>)
    2. Creates a substitution map (T → i32)
    3. Substitutes T with i32 in all variant associated types
    4. Produces a concrete EnumType with a unique name (Result<i32>)

    For each generic function instantiation (e.g., identity<i32>), this class:
    1. Takes the GenericFuncDef definition (identity<T>)
    2. Creates a substitution map (T → i32)
    3. Substitutes T with i32 in parameters and return type
    4. Produces a concrete FuncSig with a mangled name (identity__i32)

    The resulting EnumType, StructType, and FuncDef instances can be used by the rest
    of the compiler as if they were regular (non-generic) types and functions.
    """

    reporter: Reporter
    # Constraint validator for checking perk constraints on type parameters
    constraint_validator: 'ConstraintValidator | None' = None
    # Cache of already-monomorphized enum types: (base_name, type_args) → EnumType
    # Prevents re-generating the same concrete type multiple times
    cache: Dict[Tuple[str, Tuple[Type, ...]], EnumType] = field(default_factory=dict)
    # Cache of already-monomorphized struct types: (base_name, type_args) → StructType
    struct_cache: Dict[Tuple[str, Tuple[Type, ...]], StructType] = field(default_factory=dict)
    # Cache of already-monomorphized function definitions: (base_name, type_args) → FuncDef
    func_cache: Dict[Tuple[str, Tuple[Type, ...]], 'FuncDef'] = field(default_factory=dict)
    # Generic enum table for recursive monomorphization
    generic_enums: Dict[str, GenericEnumType] = field(default_factory=dict)
    # Generic struct table for recursive monomorphization
    generic_structs: Dict[str, GenericStructType] = field(default_factory=dict)
    # Generic function table for function monomorphization
    generic_funcs: Dict[str, 'GenericFuncDef'] = field(default_factory=dict)
    # Function table for registering monomorphized functions
    func_table: 'FunctionTable | None' = None
    # Track monomorphized functions for type validation (maps mangled_name → (generic_name, type_args))
    monomorphized_functions: Dict[str, Tuple[str, Tuple[Type, ...]]] = field(default_factory=dict)
    # Enum table for registering on-demand monomorphized enums
    enum_table: 'EnumTable | None' = None
    # Struct table for registering on-demand monomorphized structs
    struct_table: 'StructTable | None' = None
    # Pending instantiations worklist (for nested generic function calls)
    pending_instantiations: Set[Tuple[str, Tuple[Type, ...]]] = field(default_factory=set)

    # Specialized sub-modules (initialized lazily)
    _substitutor: TypeSubstitutor | None = field(default=None, init=False, repr=False)
    _type_monomorphizer: TypeMonomorphizer | None = field(default=None, init=False, repr=False)
    _function_monomorphizer: FunctionMonomorphizer | None = field(default=None, init=False, repr=False)

    @property
    def substitutor(self) -> TypeSubstitutor:
        """Lazy-initialize and return the type substitutor."""
        if self._substitutor is None:
            self._substitutor = TypeSubstitutor(self)
        return self._substitutor

    @property
    def type_monomorphizer(self) -> TypeMonomorphizer:
        """Lazy-initialize and return the type monomorphizer."""
        if self._type_monomorphizer is None:
            self._type_monomorphizer = TypeMonomorphizer(self)
        return self._type_monomorphizer

    @property
    def function_monomorphizer(self) -> FunctionMonomorphizer:
        """Lazy-initialize and return the function monomorphizer."""
        if self._function_monomorphizer is None:
            self._function_monomorphizer = FunctionMonomorphizer(self)
        return self._function_monomorphizer

    def _validate_type_constraints(
        self,
        type_params: Tuple,
        type_args: Tuple[Type, ...]
    ) -> None:
        """Validate perk constraints on type arguments (DRY helper).

        Args:
            type_params: Type parameters (may contain BoundedTypeParam with constraints)
            type_args: Concrete type arguments to validate

        Emits CE4006 errors for constraint violations.
        """
        if self.constraint_validator is None:
            return

        from sushi_lang.semantics.ast import BoundedTypeParam
        for param, arg in zip(type_params, type_args):
            # All params are now BoundedTypeParam (may have empty constraints list)
            if isinstance(param, BoundedTypeParam) and param.constraints:
                # Validate all constraints on this type parameter
                self.constraint_validator.validate_all_constraints(param, arg, None)

    # ===== ENUM MONOMORPHIZATION API =====

    def monomorphize_all(
        self,
        generic_enums: Dict[str, GenericEnumType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, EnumType]:
        """Monomorphize all collected generic enum instantiations.

        Delegates to TypeMonomorphizer.

        Args:
            generic_enums: Table of GenericEnumType definitions (from CollectorPass)
            instantiations: Set of (base_name, type_args) tuples (from InstantiationCollector)

        Returns:
            Dictionary mapping concrete type names (e.g., "Result<i32>") to EnumType instances
        """
        return self.type_monomorphizer.monomorphize_all_enums(generic_enums, instantiations)

    def monomorphize_enum(
        self,
        generic: GenericEnumType,
        type_args: Tuple[Type, ...]
    ) -> EnumType:
        """Create concrete enum by substituting type parameters.

        Delegates to TypeMonomorphizer.

        Args:
            generic: The generic enum definition (e.g., Result<T>)
            type_args: Concrete type arguments (e.g., (BuiltinType.I32,))

        Returns:
            Concrete EnumType with substituted types
        """
        return self.type_monomorphizer.monomorphize_enum(generic, type_args)

    # ===== STRUCT MONOMORPHIZATION API =====

    def monomorphize_all_structs(
        self,
        generic_structs: Dict[str, GenericStructType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, StructType]:
        """Monomorphize all collected generic struct instantiations.

        Delegates to TypeMonomorphizer.

        Args:
            generic_structs: Table of GenericStructType definitions (from CollectorPass)
            instantiations: Set of (base_name, type_args) tuples (from InstantiationCollector)

        Returns:
            Dictionary mapping concrete type names (e.g., "Pair<i32, string>") to StructType instances
        """
        return self.type_monomorphizer.monomorphize_all_structs(generic_structs, instantiations)

    def monomorphize_struct(
        self,
        generic: GenericStructType,
        type_args: Tuple[Type, ...]
    ) -> StructType:
        """Create concrete struct by substituting type parameters.

        Delegates to TypeMonomorphizer.

        Args:
            generic: The generic struct definition (e.g., Pair<T, U>)
            type_args: Concrete type arguments (e.g., (BuiltinType.I32, BuiltinType.STRING))

        Returns:
            Concrete StructType with substituted types
        """
        return self.type_monomorphizer.monomorphize_struct(generic, type_args)

    # ===== FUNCTION MONOMORPHIZATION API =====

    def monomorphize_function(
        self,
        generic: 'GenericFuncDef',
        type_args: Tuple[Type, ...]
    ) -> 'FuncDef':
        """Create concrete function from generic definition.

        Delegates to FunctionMonomorphizer.

        Args:
            generic: Generic function definition
            type_args: Concrete type arguments

        Returns:
            Concrete function definition (FuncDef) with substituted body
        """
        return self.function_monomorphizer.monomorphize_function(generic, type_args)

    def monomorphize_all_functions(
        self,
        function_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
        program_or_units
    ) -> None:
        """Monomorphize all detected function instantiations.

        Delegates to FunctionMonomorphizer.

        Args:
            function_instantiations: Set of (function_name, type_args) tuples
            program_or_units: Either a Program AST (single-file) or list of Units (multi-file)
        """
        self.function_monomorphizer.monomorphize_all_functions(
            function_instantiations, program_or_units
        )
