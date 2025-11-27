# semantics/generics/instantiate/__init__.py
"""
Pass 1.5: Generic Type Instantiation Collector

This pass scans the entire AST and collects all generic type instantiations
(e.g., Result<i32>, Result<MyStruct>) that are used in the program.

This enables monomorphization - generating concrete types only for
instantiations that are actually used, avoiding unnecessary code bloat.

This module provides a facade that maintains the original API while delegating
to specialized submodules for type inference, expression scanning, and function-level
collection.
"""
from __future__ import annotations
from typing import Set, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from semantics.typesys import Type
    from semantics.ast import Program

from semantics.generics.instantiate.types import TypeInferrer
from semantics.generics.instantiate.expressions import ExpressionScanner
from semantics.generics.instantiate.functions import FunctionCollector


@dataclass
class InstantiationCollector:
    """Collects all generic type instantiations used in a program.

    Scans function signatures, variable declarations, constants, and all type
    annotations to find generic type references like Result<i32> and Pair<i32, string>.

    The collected instantiations are used by the Monomorphizer to generate
    concrete EnumType and StructType instances for each unique instantiation.

    This is a facade that delegates to specialized submodules:
    - TypeInferrer: Simple type inference for literals and expressions
    - ExpressionScanner: Recursive expression traversal
    - FunctionCollector: Function-level instantiation collection
    """

    # Set of (base_name, type_args) tuples representing unique instantiations
    # Examples:
    #   - ("Result", (BuiltinType.I32,)) for Result<i32>
    #   - ("Pair", (BuiltinType.I32, BuiltinType.STRING)) for Pair<i32, string>
    # The base_name distinguishes between generic enums and generic structs.
    instantiations: Set[Tuple[str, Tuple["Type", ...]]] = field(default_factory=set)

    # NEW: Set of (function_name, type_args) tuples for generic function instantiations
    # Examples:
    #   - ("identity", (BuiltinType.I32,)) for identity<i32>
    #   - ("swap", (BuiltinType.I32, BuiltinType.STRING)) for swap<i32, string>
    function_instantiations: Set[Tuple[str, Tuple["Type", ...]]] = field(default_factory=set)

    # Struct table for resolving UnknownType to StructType
    struct_table: dict | None = field(default=None)

    # Enum table for resolving UnknownType to EnumType
    enum_table: dict | None = field(default=None)

    # Generic struct table for checking if a base_name refers to a generic struct
    # This is used to distinguish generic struct instantiations from generic enum instantiations
    generic_structs: dict | None = field(default=None)

    # NEW: Generic function table for checking if a function name refers to a generic function
    generic_funcs: dict | None = field(default=None)

    # Simple variable type table for tracking explicitly typed variables in current scope
    # Maps variable name -> type for variables with explicit type annotations
    variable_types: dict[str, "Type"] = field(default_factory=dict)

    # Track visited types to prevent infinite recursion on recursive types (e.g., Own<Expr> in Expr)
    visited_types: Set[str] = field(default_factory=set)

    def run(self, program: "Program") -> Tuple[Set[Tuple[str, Tuple["Type", ...]]], Set[Tuple[str, Tuple["Type", ...]]]]:
        """Entry point for instantiation collection.

        Args:
            program: The program AST to scan

        Returns:
            Tuple of (type instantiations, function instantiations)
            - type instantiations: Set of (base_name, type_args) for generic types
            - function instantiations: Set of (function_name, type_args) for generic functions
        """
        # Initialize specialized helpers
        type_inferrer = TypeInferrer(
            variable_types=self.variable_types,
            struct_table=self.struct_table or {},
            enum_table=self.enum_table or {},
        )

        expression_scanner = ExpressionScanner(
            type_inferrer=type_inferrer,
            instantiations=self.instantiations,
            function_instantiations=self.function_instantiations,
            generic_funcs=self.generic_funcs or {},
        )

        function_collector = FunctionCollector(
            expression_scanner=expression_scanner,
            instantiations=self.instantiations,
            variable_types=self.variable_types,
            visited_types=self.visited_types,
        )

        # Collect from constants
        for const in program.constants:
            function_collector.collect_from_const(const)

        # Collect from struct definitions
        # This ensures that generic types used as struct fields (e.g., Maybe<i32>)
        # are properly monomorphized before codegen
        for struct in program.structs:
            function_collector.collect_from_struct(struct)

        # Collect from enum definitions
        # This ensures that generic types used in enum variants (e.g., Own<Expr>)
        # are properly monomorphized before codegen
        for enum in program.enums:
            function_collector.collect_from_enum(enum)

        # Collect from function signatures
        for func in program.functions:
            function_collector.collect_from_function(func)

        # Collect from extension method signatures
        for ext in program.extensions:
            function_collector.collect_from_extension(ext)

        # Collect from perk implementation methods
        # Perk methods return bare types (like extensions), but we still need to
        # collect generic instantiations from their parameters and bodies
        for perk_impl in program.perk_impls:
            function_collector.collect_from_perk_impl(perk_impl)

        return self.instantiations, self.function_instantiations
