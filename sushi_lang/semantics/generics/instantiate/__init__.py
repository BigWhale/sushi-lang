"""Pass 1.5: Generic Type Instantiation Collector"""
from __future__ import annotations
from typing import Set, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.ast import Program

from sushi_lang.semantics.generics.instantiate.types import TypeInferrer
from sushi_lang.semantics.generics.instantiate.expressions import ExpressionScanner
from sushi_lang.semantics.generics.instantiate.functions import FunctionCollector


@dataclass
class InstantiationCollector:
    """Collects all generic type instantiations used in a program."""

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

    struct_table: dict | None = field(default=None)

    enum_table: dict | None = field(default=None)

    # Generic struct table for checking if a base_name refers to a generic struct
    # This is used to distinguish generic struct instantiations from generic enum instantiations
    generic_structs: dict | None = field(default=None)

    generic_funcs: dict | None = field(default=None)

    # Plain (non-generic) top-level function table (name -> FuncSig), used to present a
    # FunctionType for a bare function reference passed as a higher-order argument.
    func_table: dict | None = field(default=None)

    # The whole-program SymbolTables. When present, Pass 1.5 infers generic-call
    # argument and receiver types through Pass 2's own TypeValidator instead of a thin
    # parallel inferrer -- the two used to disagree, and every method Pass 2 knew and
    # Pass 1.5 did not dropped an instantiation on the floor (CE2061; issues #171/#191).
    tables: object | None = field(default=None)

    variable_types: dict[str, "Type"] = field(default_factory=dict)

    visited_types: Set[str] = field(default_factory=set)

    def run(self, program: "Program") -> Tuple[Set[Tuple[str, Tuple["Type", ...]]], Set[Tuple[str, Tuple["Type", ...]]]]:
        """Entry point for instantiation collection."""
        type_inferrer = TypeInferrer(
            variable_types=self.variable_types,
            struct_table=self.struct_table or {},
            enum_table=self.enum_table or {},
            func_table=self.func_table or {},
        )

        # Build Pass 2's real inferrer over the same tables, with a discard reporter so
        # any diagnostics it raises never reach the user (they belong to Pass 2, which
        # runs later and emits them for real). It shares this collector's variable_types
        # dict, so the scope the collector builds as it walks is the scope the inferrer
        # sees. Constructed only when the whole SymbolTables is available.
        type_validator = self._build_shared_inferrer()

        expression_scanner = ExpressionScanner(
            type_inferrer=type_inferrer,
            instantiations=self.instantiations,
            function_instantiations=self.function_instantiations,
            generic_funcs=self.generic_funcs or {},
            type_validator=type_validator,
        )

        function_collector = FunctionCollector(
            expression_scanner=expression_scanner,
            instantiations=self.instantiations,
            variable_types=self.variable_types,
            visited_types=self.visited_types,
        )

        expression_scanner.scan_block = function_collector._collect_from_block

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

        for func in program.functions:
            function_collector.collect_from_function(func)

        for ext in program.extensions:
            function_collector.collect_from_extension(ext)

        # Collect from perk implementation methods
        # Perk methods return bare types (like extensions), but we still need to
        # collect generic instantiations from their parameters and bodies
        for perk_impl in program.perk_impls:
            function_collector.collect_from_perk_impl(perk_impl)

        return self.instantiations, self.function_instantiations

    def _build_shared_inferrer(self):
        """Pass 2's TypeValidator over the same tables, wired to discard diagnostics."""
        if self.tables is None:
            return None
        from sushi_lang.internals.report import Reporter
        from sushi_lang.semantics.passes.types import TypeValidator

        validator = TypeValidator(Reporter(), self.tables)
        validator.variable_types = self.variable_types
        return validator
