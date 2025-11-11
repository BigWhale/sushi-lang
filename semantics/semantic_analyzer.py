# semantics/semantic_analyzer.py
from __future__ import annotations
from typing import Optional, Any, List

from internals.report import Reporter
from semantics.ast import Program
from semantics.passes.collect import CollectorPass, ConstantTable, StructTable, EnumTable, GenericEnumTable, GenericStructTable, PerkTable, PerkImplementationTable, FunctionTable, ExtensionTable, GenericFunctionTable
from semantics.passes.scope import ScopeAnalyzer
from semantics.passes.types import TypeValidator
from semantics.passes.borrow import BorrowChecker
from semantics.units import UnitManager, Unit
from semantics.typesys import BuiltinType


class SemanticAnalyzer:
    """
    Semantic analysis coordinator that runs all semantic analysis passes.

    Pass execution order:
      - Pass 0: Symbol collection (constants, structs, enums, generics, functions, extensions)
      - Pass 1.5: Generic instantiation collection (find all Result<T> usages)
      - Pass 1.6: Monomorphization (generate concrete types from generics)
      - Pass 1.7: AST transformation (resolve EnumConstructor vs MethodCall ambiguity)
      - Pass 1.8: Hash registration (auto-derive .hash() for structs)
      - Pass 1: Scope analysis (variable lifecycle, usage tracking)
      - Pass 2: Type validation (type checking, inference, compatibility)
      - Pass 3: Borrow checking (reference safety, ownership validation)

    Supports both single-file and multi-file compilation modes.
    """

    def __init__(self, reporter: Reporter, filename: str = "<input>", unit_manager: Optional[UnitManager] = None) -> None:
        self.reporter = reporter
        self.filename = filename
        self.unit_manager = unit_manager
        self.constants: Optional[ConstantTable] = None
        self.structs: Optional[StructTable] = None
        self.enums: Optional[EnumTable] = None
        self.generic_enums: Optional[GenericEnumTable] = None
        self.generic_structs: Optional[GenericStructTable] = None
        self.perks: Optional[PerkTable] = None
        self.perk_impls: Optional[PerkImplementationTable] = None
        self.funcs: Optional[FunctionTable] = None
        self.extensions: Optional[ExtensionTable] = None
        self.generic_extensions: Optional['GenericExtensionTable'] = None
        self.generic_funcs: Optional[GenericFunctionTable] = None
        self.monomorphized_extensions: list['ExtendDef'] = []  # Concrete ExtendDef nodes for codegen
        self.main_expects_args: bool = False  # Whether main function has string[] args parameter

    def check(self, program: Program) -> None:
        """
        Entry point for semantic analysis. Runs all semantic analysis passes in sequence.
        Handles both single-file and multi-file compilation modes.
        """
        if self.unit_manager is not None:
            # Multi-file mode: analyze all units in compilation order
            self._check_multi_file()
        else:
            # Single-file mode: analyze just this program
            self._check_single_file(program)

    def _check_single_file(self, program: Program) -> None:
        """Single-file semantic analysis (original behavior)."""
        # Pass 0: collect constants, structs, enums, perks, function headers and extension methods
        collector = CollectorPass(self.reporter)
        self.constants, self.structs, self.enums, self.generic_enums, self.generic_structs, self.perks, self.perk_impls, self.funcs, self.extensions, self.generic_extensions, self.generic_funcs = collector.run(program)

        # Check if main function expects command line arguments
        self._check_main_function_args(program)

        # Pass 1.5: collect generic type instantiations (e.g., Result<i32>, Pair<i32, string>)
        from semantics.generics.instantiate import InstantiationCollector
        instantiation_collector = InstantiationCollector(
            struct_table=self.structs.by_name,
            enum_table=self.enums.by_name,
            generic_structs=self.generic_structs.by_name,
            generic_funcs=self.generic_funcs.by_name
        )
        type_instantiations, func_instantiations = instantiation_collector.run(program)

        # Pass 1.6: monomorphize generic types into concrete types
        from semantics.generics.monomorphize import Monomorphizer
        from semantics.generics.constraints import ConstraintValidator

        # Create constraint validator for perk constraint checking (Phase 4)
        constraint_validator = ConstraintValidator(
            perk_table=self.perks,
            perk_impl_table=self.perk_impls,
            reporter=self.reporter
        )

        monomorphizer = Monomorphizer(
            reporter=self.reporter,
            constraint_validator=constraint_validator
        )

        # Separate enum and struct instantiations
        enum_instantiations = set()
        struct_instantiations = set()
        for base_name, type_args in instantiations:
            if base_name in self.generic_enums.by_name:
                enum_instantiations.add((base_name, type_args))
            elif base_name in self.generic_structs.by_name:
                struct_instantiations.add((base_name, type_args))

        # Monomorphize generic enums
        concrete_enums = monomorphizer.monomorphize_all(self.generic_enums.by_name, enum_instantiations)

        # Merge monomorphized concrete enums into the enum table
        # This allows the rest of the compiler to treat them as regular enums
        for enum_name, enum_type in concrete_enums.items():
            self.enums.by_name[enum_name] = enum_type
            self.enums.order.append(enum_name)

        # Phase 3: Monomorphize generic functions
        # Set up function monomorphization
        monomorphizer.generic_funcs = self.generic_funcs.by_name
        monomorphizer.generic_enums = self.generic_enums.by_name
        monomorphizer.generic_structs = self.generic_structs.by_name
        monomorphizer.func_table = self.funcs
        monomorphizer.enum_table = self.enums
        monomorphizer.struct_table = self.structs
        # Monomorphize all detected function instantiations (single-file mode)
        # The monomorphizer will recursively detect and monomorphize nested calls
        monomorphizer.monomorphize_all_functions(func_instantiations, prog)

        # Monomorphize generic structs
        concrete_structs = monomorphizer.monomorphize_all_structs(self.generic_structs.by_name, struct_instantiations)

        # Merge monomorphized concrete structs into the struct table
        # This allows the rest of the compiler to treat them as regular structs
        for struct_name, struct_type in concrete_structs.items():
            self.structs.by_name[struct_name] = struct_type
            self.structs.order.append(struct_name)

        # Pass 1.7: Resolve struct field types and enum variant types (UnknownType → concrete types)
        # This runs AFTER monomorphization so all struct/enum types exist in the tables
        # Resolves nested struct references (e.g., Rectangle.top_left: Point)
        # Resolves enum variant associated types (e.g., Response.Success: Status)
        from semantics.passes.ast_transform import resolve_struct_field_types, resolve_enum_variant_types
        resolve_struct_field_types(self.structs, self.enums)
        resolve_enum_variant_types(self.structs, self.enums)

        # Pass 1.8: Register hash methods for all hashable structs, enums, and arrays
        # This runs AFTER type resolution so nested struct/enum types are fully resolved
        # Works for both generic and non-generic types (after monomorphization)
        # Order matters: structs/enums first (dependencies), then arrays (which may contain them)
        from semantics.passes.hash_registration import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes
        )
        register_all_struct_hashes(self.structs)

        # Register enum hash methods (with proper error reporting for direct recursion)
        register_all_enum_hashes(self.enums, self.reporter)

        register_all_array_hashes(self.structs, self.enums)

        # Monomorphize generic extension methods
        from backend.generics.extensions import monomorphize_all_extension_methods
        concrete_extension_defs = monomorphize_all_extension_methods(
            self.generic_extensions.by_type,
            struct_instantiations,
            concrete_structs
        )

        # Store monomorphized ExtendDef nodes for backend codegen
        # Key is (target_type_name, method_name, type_args), value is ExtendDef
        for (target_type_name, method_name, type_args), extend_def in concrete_extension_defs.items():
            self.monomorphized_extensions.append(extend_def)
            # Add to extension table for method lookup during type validation
            from semantics.passes.collect import ExtensionMethod
            extension_method = ExtensionMethod(
                target_type=extend_def.target_type,
                name=extend_def.name,
                params=extend_def.params,
                ret_type=extend_def.ret
            )
            self.extensions.add_method(extension_method)

        # Pass 1: scope and variable usage analysis
        scope_analyzer = ScopeAnalyzer(self.reporter, self.constants, self.structs, self.enums, self.generic_enums, self.generic_structs)
        scope_analyzer.run(program)

        # Pass 2: type validation
        type_validator = TypeValidator(self.reporter, self.constants, self.structs, self.enums, self.funcs, self.extensions, self.generic_enums, self.generic_structs, self.perks, self.perk_impls, self.generic_extensions, self.generic_funcs, monomorphized_functions=monomorphizer.monomorphized_functions)
        type_validator.run(program)

        # Validate monomorphized generic extension methods
        for extend_def in self.monomorphized_extensions:
            type_validator._validate_extension_method(extend_def)

        # Pass 3: borrow checking (reference safety)
        borrow_checker = BorrowChecker(self.reporter, struct_names=self.structs.by_name if self.structs else None)
        borrow_checker.run(program)

    def _check_multi_file(self) -> None:
        """Multi-file semantic analysis with cross-unit symbol resolution."""
        if self.unit_manager is None:
            return

        # Get units in compilation order
        compilation_order = self.unit_manager.get_compilation_order()
        if compilation_order is None:
            return  # Error already reported

        # Phase 0: Collect symbols from all units
        collector = CollectorPass(self.reporter)
        global_constants = ConstantTable()
        global_structs = StructTable()
        global_enums = EnumTable()
        global_generic_enums = GenericEnumTable()
        global_generic_structs = GenericStructTable()
        global_perks = PerkTable()
        global_perk_impls = PerkImplementationTable()
        global_funcs = FunctionTable()
        global_extensions = ExtensionTable()
        from semantics.passes.collect import GenericExtensionTable
        global_generic_extensions = GenericExtensionTable()
        global_generic_funcs = GenericFunctionTable()

        for unit in compilation_order:
            if unit.ast is None:
                continue

            # Collect symbols from this unit, passing unit name for visibility tracking
            unit_constants, unit_structs, unit_enums, unit_generic_enums, unit_generic_structs, unit_perks, unit_perk_impls, unit_funcs, unit_extensions, unit_generic_extensions, unit_generic_funcs = collector.run(unit.ast, unit_name=unit.name)

            # Merge unit symbols into global tables, considering visibility
            self._merge_unit_symbols(unit, unit_constants, unit_structs, unit_enums, unit_generic_enums, unit_generic_structs, unit_perks, unit_perk_impls, unit_funcs, unit_extensions, unit_generic_extensions, unit_generic_funcs,
                                   global_constants, global_structs, global_enums, global_generic_enums, global_generic_structs, global_perks, global_perk_impls, global_funcs, global_extensions, global_generic_extensions, global_generic_funcs)

        self.constants = global_constants
        self.structs = global_structs
        self.enums = global_enums
        self.generic_enums = global_generic_enums
        self.generic_structs = global_generic_structs
        self.perks = global_perks
        self.perk_impls = global_perk_impls
        self.funcs = global_funcs
        self.extensions = global_extensions
        self.generic_extensions = global_generic_extensions
        self.generic_funcs = global_generic_funcs

        # Check if main function expects command line arguments (across all units)
        self._check_main_function_args_multi_file(compilation_order)

        # Pass 1.5: collect generic type instantiations from all units
        from semantics.generics.instantiate import InstantiationCollector
        instantiation_collector = InstantiationCollector(
            struct_table=self.structs.by_name,
            enum_table=self.enums.by_name,
            generic_structs=self.generic_structs.by_name,
            generic_funcs=self.generic_funcs.by_name
        )
        for unit in compilation_order:
            if unit.ast is not None:
                instantiation_collector.run(unit.ast)
        type_instantiations = instantiation_collector.instantiations
        func_instantiations = instantiation_collector.function_instantiations

        # Pass 1.6: monomorphize generic types into concrete types
        from semantics.generics.monomorphize import Monomorphizer
        from semantics.generics.constraints import ConstraintValidator

        # Create constraint validator for perk constraint checking (Phase 4)
        constraint_validator = ConstraintValidator(
            perk_table=self.perks,
            perk_impl_table=self.perk_impls,
            reporter=self.reporter
        )

        monomorphizer = Monomorphizer(
            reporter=self.reporter,
            constraint_validator=constraint_validator
        )

        # Separate enum and struct instantiations
        enum_instantiations = set()
        struct_instantiations = set()
        for base_name, type_args in type_instantiations:
            if base_name in self.generic_enums.by_name:
                enum_instantiations.add((base_name, type_args))
            elif base_name in self.generic_structs.by_name:
                struct_instantiations.add((base_name, type_args))

        # Phase 3: Monomorphize generic functions
        # Set up function monomorphization
        monomorphizer.generic_funcs = self.generic_funcs.by_name
        monomorphizer.generic_enums = self.generic_enums.by_name
        monomorphizer.generic_structs = self.generic_structs.by_name
        monomorphizer.func_table = self.funcs
        monomorphizer.enum_table = self.enums
        monomorphizer.struct_table = self.structs
        # Monomorphize all detected function instantiations (multi-file mode)
        monomorphizer.monomorphize_all_functions(func_instantiations, compilation_order)

        # Monomorphize generic enums
        concrete_enums = monomorphizer.monomorphize_all(self.generic_enums.by_name, enum_instantiations)

        # Merge monomorphized concrete enums into the global enum table
        for enum_name, enum_type in concrete_enums.items():
            self.enums.by_name[enum_name] = enum_type
            self.enums.order.append(enum_name)

        # Monomorphize generic structs
        concrete_structs = monomorphizer.monomorphize_all_structs(self.generic_structs.by_name, struct_instantiations)

        # Merge monomorphized concrete structs into the global struct table
        for struct_name, struct_type in concrete_structs.items():
            self.structs.by_name[struct_name] = struct_type
            self.structs.order.append(struct_name)

        # Pass 1.7: Resolve struct field types and enum variant types (UnknownType → concrete types)
        # This runs AFTER monomorphization so all struct/enum types exist in the tables
        # Resolves nested struct references (e.g., Rectangle.top_left: Point)
        # Resolves enum variant associated types (e.g., Response.Success: Status)
        from semantics.passes.ast_transform import resolve_struct_field_types, resolve_enum_variant_types
        resolve_struct_field_types(self.structs, self.enums)
        resolve_enum_variant_types(self.structs, self.enums)

        # Pass 1.8: Register hash methods for all hashable structs, enums, and arrays
        # This runs AFTER type resolution so nested struct/enum types are fully resolved
        # Works for both generic and non-generic types (after monomorphization)
        # Order matters: structs/enums first (dependencies), then arrays (which may contain them)
        from semantics.passes.hash_registration import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes
        )
        register_all_struct_hashes(self.structs)

        # Register enum hash methods (with proper error reporting for direct recursion)
        register_all_enum_hashes(self.enums, self.reporter)

        register_all_array_hashes(self.structs, self.enums)

        # Monomorphize generic extension methods
        from backend.generics.extensions import monomorphize_all_extension_methods
        concrete_extension_defs = monomorphize_all_extension_methods(
            self.generic_extensions.by_type,
            struct_instantiations,
            concrete_structs
        )

        # Store monomorphized ExtendDef nodes for backend codegen
        for (target_type_name, method_name, type_args), extend_def in concrete_extension_defs.items():
            self.monomorphized_extensions.append(extend_def)
            # Add to extension table for method lookup during type validation
            from semantics.passes.collect import ExtensionMethod
            extension_method = ExtensionMethod(
                target_type=extend_def.target_type,
                name=extend_def.name,
                params=extend_def.params,
                ret_type=extend_def.ret
            )
            self.extensions.add_method(extension_method)

        # Phase 1 & 2: Run scope and type analysis on all units with global context
        # Unlike single-file mode, we need to analyze all units together since they can reference each other
        # However, we use unit-specific reporters to properly attribute errors to the correct files

        for unit in compilation_order:
            if unit.ast is None:
                continue

            # Create unit-specific reporter with the unit's file path and source
            try:
                unit_source = unit.file_path.read_text(encoding="utf-8")
            except Exception:
                unit_source = ""  # Fallback if we can't read the source

            unit_reporter = Reporter(source=unit_source, filename=str(unit.file_path))

            # Pass 1: scope analysis with global constants, structs, and enums (unit-specific reporter)
            scope_analyzer = ScopeAnalyzer(unit_reporter, self.constants, self.structs, self.enums, self.generic_enums, self.generic_structs)
            scope_analyzer.run(unit.ast)

            # Pass 2: type validation with global symbols (unit-specific reporter)
            type_validator = TypeValidator(unit_reporter, self.constants, self.structs, self.enums, self.funcs, self.extensions, self.generic_enums, self.generic_structs, self.perks, self.perk_impls, self.generic_extensions, self.generic_funcs, current_unit_name=unit.name, monomorphized_functions=monomorphizer.monomorphized_functions)
            type_validator.run(unit.ast)

            # Pass 3: borrow checking (unit-specific reporter)
            borrow_checker = BorrowChecker(unit_reporter, struct_names=self.structs.by_name if self.structs else None)
            borrow_checker.run(unit.ast)

            # Merge unit reporter results into main reporter
            self.reporter.items.extend(unit_reporter.items)

        # Validate monomorphized generic extension methods (use main reporter for now)
        if self.monomorphized_extensions:
            # Create a type validator with global context
            type_validator = TypeValidator(self.reporter, self.constants, self.structs, self.enums, self.funcs, self.extensions, self.generic_enums, self.generic_structs, self.perks, self.perk_impls, self.generic_extensions)
            for extend_def in self.monomorphized_extensions:
                type_validator._validate_extension_method(extend_def)


    def _merge_unit_symbols(self, unit: Unit, unit_constants: ConstantTable, unit_structs: StructTable, unit_enums: EnumTable,
                          unit_generic_enums: GenericEnumTable, unit_generic_structs: GenericStructTable, unit_perks: PerkTable, unit_perk_impls: PerkImplementationTable, unit_funcs: FunctionTable, unit_extensions: ExtensionTable, unit_generic_extensions: 'GenericExtensionTable', unit_generic_funcs: GenericFunctionTable,
                          global_constants: ConstantTable, global_structs: StructTable, global_enums: EnumTable,
                          global_generic_enums: GenericEnumTable, global_generic_structs: GenericStructTable, global_perks: PerkTable, global_perk_impls: PerkImplementationTable, global_funcs: FunctionTable, global_extensions: ExtensionTable, global_generic_extensions: 'GenericExtensionTable', global_generic_funcs: GenericFunctionTable) -> None:
        """Merge symbols from a unit into global tables, respecting visibility rules."""
        # Merge constants (all constants are global by design)
        for name, const_sig in unit_constants.by_name.items():
            if name not in global_constants.by_name:
                global_constants.by_name[name] = const_sig
                global_constants.order.append(name)

        # Merge structs (all structs are global - visible across units)
        for name, struct_type in unit_structs.by_name.items():
            if name not in global_structs.by_name:
                global_structs.by_name[name] = struct_type
                global_structs.order.append(name)

        # Merge enums (all enums are global - visible across units)
        for name, enum_type in unit_enums.by_name.items():
            if name not in global_enums.by_name:
                global_enums.by_name[name] = enum_type
                global_enums.order.append(name)

        # Merge generic enums (all generic enums are global - visible across units)
        for name, generic_enum in unit_generic_enums.by_name.items():
            if name not in global_generic_enums.by_name:
                global_generic_enums.by_name[name] = generic_enum
                global_generic_enums.order.append(name)

        # Merge generic structs (all generic structs are global - visible across units)
        for name, generic_struct in unit_generic_structs.by_name.items():
            if name not in global_generic_structs.by_name:
                global_generic_structs.by_name[name] = generic_struct
                global_generic_structs.order.append(name)

        # Merge perks (all perks are global - visible across units)
        for name, perk_def in unit_perks.by_name.items():
            if name not in global_perks.by_name:
                global_perks.by_name[name] = perk_def
                global_perks.order.append(name)

        # Merge perk implementations (all perk implementations are global - visible across units)
        for key, impl in unit_perk_impls.implementations.items():
            if key not in global_perk_impls.implementations:
                type_name, perk_name = key
                global_perk_impls.implementations[key] = impl
                # Update indexes
                if type_name not in global_perk_impls.by_type:
                    global_perk_impls.by_type[type_name] = set()
                global_perk_impls.by_type[type_name].add(perk_name)
                if perk_name not in global_perk_impls.by_perk:
                    global_perk_impls.by_perk[perk_name] = set()
                global_perk_impls.by_perk[perk_name].add(type_name)

        # Merge functions (both public and private functions are tracked)
        # Note: Visibility checking is done during type validation, not during merge
        # All functions need to be in the global table so that we can check visibility
        for name, func_sig in unit_funcs.by_name.items():
            # Add function to global table (visibility is checked later during validation)
            if name not in global_funcs.by_name:
                global_funcs.by_name[name] = func_sig
                global_funcs.order.append(name)

        # Merge stdlib functions (registered from use <module> statements)
        for key, stdlib_func in unit_funcs._stdlib_functions.items():
            if key not in global_funcs._stdlib_functions:
                global_funcs._stdlib_functions[key] = stdlib_func

        # Merge extension methods (all extension methods are global)
        for target_type, methods in unit_extensions.by_type.items():
            if target_type not in global_extensions.by_type:
                global_extensions.by_type[target_type] = {}
            for method_name, method in methods.items():
                # Extension methods are always global, but we should check for conflicts
                if method_name not in global_extensions.by_type[target_type]:
                    global_extensions.by_type[target_type][method_name] = method

        # Merge generic extension methods (all generic extension methods are global)
        for base_type_name, methods in unit_generic_extensions.by_type.items():
            if base_type_name not in global_generic_extensions.by_type:
                global_generic_extensions.by_type[base_type_name] = {}
            for method_name, method in methods.items():
                # Generic extension methods are always global
                if method_name not in global_generic_extensions.by_type[base_type_name]:
                    global_generic_extensions.by_type[base_type_name][method_name] = method

        # Merge generic functions (all generic functions are global)
        for name, generic_func in unit_generic_funcs.by_name.items():
            if name not in global_generic_funcs.by_name:
                global_generic_funcs.by_name[name] = generic_func
                global_generic_funcs.order.append(name)

    def _check_main_function_args(self, program: Program) -> None:
        """
        Check if the main function has a string[] args parameter.

        Looks for exactly: `fn main(..., string[] args, ...)` where args must be named "args"
        and have type string[]. Other parameters are ignored.
        """
        main_func = None
        for func in program.functions:
            if func.name == "main":
                main_func = func
                break

        self._process_main_function_for_args(main_func)

    def _check_main_function_args_multi_file(self, compilation_order: list[Unit]) -> None:
        """
        Check if the main function has a string[] args parameter in multi-file mode.

        Searches for main function across all units and checks if it has the args parameter.
        """
        main_func = None
        # Search for main function across all units
        for unit in compilation_order:
            if unit.ast is None:
                continue
            for func in unit.ast.functions:
                if func.name == "main":
                    main_func = func
                    break
            if main_func is not None:
                break

        self._process_main_function_for_args(main_func)

    def _process_main_function_for_args(self, main_func) -> None:
        """
        Process a main function to check if it has a string[] args parameter.

        Args:
            main_func: The main function AST node, or None if not found.
        """
        from semantics.typesys import DynamicArrayType, BuiltinType

        if main_func is None:
            # No main function found, no args processing needed
            self.main_expects_args = False
            return

        # Check if main function has a parameter named "args" of type string[]
        for param in main_func.params:
            if (param.name == "args" and
                isinstance(param.ty, DynamicArrayType) and
                param.ty.base_type == BuiltinType.STRING):
                self.main_expects_args = True
                return

        # No string[] args parameter found
        self.main_expects_args = False
