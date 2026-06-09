# semantics/semantic_analyzer.py
from __future__ import annotations
from typing import Optional, Any, List

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import Program
from sushi_lang.semantics.passes.collect import CollectorPass, ConstantTable, StructTable, EnumTable, GenericEnumTable, GenericStructTable, PerkTable, PerkImplementationTable, FunctionTable, ExtensionTable, GenericFunctionTable
from sushi_lang.semantics.passes.scope import ScopeAnalyzer
from sushi_lang.semantics.passes.types import TypeValidator
from sushi_lang.semantics.passes.borrow import BorrowChecker
from sushi_lang.semantics.units import UnitManager, Unit
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.symbol_merger import SymbolTableMerger


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

    def __init__(self, reporter: Reporter, filename: str = "<input>", unit_manager: Optional[UnitManager] = None, library_linker: Optional['LibraryLinker'] = None, library_registry: Optional['LibraryRegistry'] = None) -> None:
        self.reporter = reporter
        self.filename = filename
        self.unit_manager = unit_manager
        self.library_linker = library_linker
        self.library_registry = library_registry
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
        self.externals = collector.externals

        # FFI: validate external signatures (CE5003) and emit CW5001
        from sushi_lang.semantics.passes.types.externals import validate_external_signatures
        validate_external_signatures(self.reporter, program)

        # Check if main function expects command line arguments
        self._check_main_function_args(program)

        # Pass 1.5: collect generic type instantiations (e.g., Result<i32>, Pair<i32, string>)
        from sushi_lang.semantics.generics.instantiate import InstantiationCollector
        instantiation_collector = InstantiationCollector(
            struct_table=self.structs.by_name,
            enum_table=self.enums.by_name,
            generic_structs=self.generic_structs.by_name,
            generic_funcs=self.generic_funcs.by_name
        )
        type_instantiations, func_instantiations = instantiation_collector.run(program)

        # Pass 1.6: monomorphize generic types into concrete types
        from sushi_lang.semantics.generics.monomorphize import Monomorphizer
        from sushi_lang.semantics.generics.constraints import ConstraintValidator

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
            # Skip Result<T, E> - it's handled specially as ResultType, not monomorphized
            if base_name == "Result" and len(type_args) == 2:
                continue
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
        from sushi_lang.semantics.passes.ast_transform import resolve_struct_field_types, resolve_enum_variant_types
        resolve_struct_field_types(self.structs, self.enums)
        resolve_enum_variant_types(self.structs, self.enums)

        # Pass 1.8: Register hash methods for all hashable structs, enums, and arrays
        # This runs AFTER type resolution so nested struct/enum types are fully resolved
        # Works for both generic and non-generic types (after monomorphization)
        # Order matters: structs/enums first (dependencies), then arrays (which may contain them)
        from sushi_lang.semantics.passes.hash_registration import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes
        )
        register_all_struct_hashes(self.structs)

        # Register enum hash methods (with proper error reporting for direct recursion)
        register_all_enum_hashes(self.enums, self.reporter)

        register_all_array_hashes(self.structs, self.enums)

        # Monomorphize generic extension methods
        from sushi_lang.backend.generics.extensions import monomorphize_all_extension_methods
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
            from sushi_lang.semantics.passes.collect import ExtensionMethod
            extension_method = ExtensionMethod(
                target_type=extend_def.target_type,
                name=extend_def.name,
                params=extend_def.params,
                ret_type=extend_def.ret
            )
            self.extensions.add_method(extension_method)

        # Pass 1: scope and variable usage analysis
        scope_analyzer = ScopeAnalyzer(self.reporter, self.constants, self.structs, self.enums, self.generic_enums, self.generic_structs, external_table=self.externals)
        scope_analyzer.run(program)

        # Pass 2: type validation
        type_validator = TypeValidator(self.reporter, self.constants, self.structs, self.enums, self.funcs, self.extensions, self.generic_enums, self.generic_structs, self.perks, self.perk_impls, self.generic_extensions, self.generic_funcs, monomorphized_functions=monomorphizer.monomorphized_functions, external_table=self.externals)
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
        from sushi_lang.semantics.passes.collect import GenericExtensionTable
        global_generic_extensions = GenericExtensionTable()
        global_generic_funcs = GenericFunctionTable()

        symbol_merger = SymbolTableMerger()

        # Seed shipped library perk DEFINITIONS into the shared collector's perk
        # table BEFORE collecting the consumer's units. Perk-impl collection
        # (`extend T with Perk`) validates each impl against the visible perk
        # definitions at collection time (CE4003), so a consumer that supplies
        # an impl for a library-shipped perk must see that perk's contract here.
        if self.library_linker is not None:
            self._seed_library_perks(collector.perks)

        for unit in compilation_order:
            if unit.ast is None:
                continue

            # Collect symbols from this unit, passing unit name for visibility tracking
            unit_constants, unit_structs, unit_enums, unit_generic_enums, unit_generic_structs, unit_perks, unit_perk_impls, unit_funcs, unit_extensions, unit_generic_extensions, unit_generic_funcs = collector.run(unit.ast, unit_name=unit.name)

            # Merge unit symbols into global tables, considering visibility
            symbol_merger.merge_all(
                unit, unit_constants, unit_structs, unit_enums, unit_generic_enums,
                unit_generic_structs, unit_perks, unit_perk_impls, unit_funcs,
                unit_extensions, unit_generic_extensions, unit_generic_funcs,
                global_constants, global_structs, global_enums, global_generic_enums,
                global_generic_structs, global_perks, global_perk_impls, global_funcs,
                global_extensions, global_generic_extensions, global_generic_funcs
            )

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
        # FFI externals accumulate across all units in the shared collector table.
        self.externals = collector.externals

        # FFI: validate external signatures (CE5003) and emit CW5001 per unit
        from sushi_lang.semantics.passes.types.externals import validate_external_signatures
        for unit in compilation_order:
            if unit.ast is not None:
                validate_external_signatures(self.reporter, unit.ast)

        # Register library types if any libraries are loaded
        # Order: structs first, then enums (may reference structs), then functions (may reference both)
        if self.library_linker is not None and self.library_registry is None:
            self._build_library_registry()
        if self.library_registry is not None or self.library_linker is not None:
            self._register_library_structs()
            self._register_library_enums()
            self._register_library_functions()
            # NOTE: shipped library perk DEFINITIONS are seeded earlier, before
            # the consumer's perk-impl collection (see _seed_library_perks call
            # in the Phase 0 loop above); they are already in self.perks here.
            self._register_library_generic_functions()

        # Check if main function expects command line arguments (across all units)
        self._check_main_function_args_multi_file(compilation_order)

        # Pass 1.5: collect generic type instantiations from all units
        from sushi_lang.semantics.generics.instantiate import InstantiationCollector
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
        from sushi_lang.semantics.generics.monomorphize import Monomorphizer
        from sushi_lang.semantics.generics.constraints import ConstraintValidator

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
            # Skip Result<T, E> - it's handled specially as ResultType, not monomorphized
            if base_name == "Result" and len(type_args) == 2:
                continue
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
        from sushi_lang.semantics.passes.ast_transform import resolve_struct_field_types, resolve_enum_variant_types
        resolve_struct_field_types(self.structs, self.enums)
        resolve_enum_variant_types(self.structs, self.enums)

        # Pass 1.8: Register hash methods for all hashable structs, enums, and arrays
        # This runs AFTER type resolution so nested struct/enum types are fully resolved
        # Works for both generic and non-generic types (after monomorphization)
        # Order matters: structs/enums first (dependencies), then arrays (which may contain them)
        from sushi_lang.semantics.passes.hash_registration import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes
        )
        register_all_struct_hashes(self.structs)

        # Register enum hash methods (with proper error reporting for direct recursion)
        register_all_enum_hashes(self.enums, self.reporter)

        register_all_array_hashes(self.structs, self.enums)

        # Monomorphize generic extension methods
        from sushi_lang.backend.generics.extensions import monomorphize_all_extension_methods
        concrete_extension_defs = monomorphize_all_extension_methods(
            self.generic_extensions.by_type,
            struct_instantiations,
            concrete_structs
        )

        # Store monomorphized ExtendDef nodes for backend codegen
        for (target_type_name, method_name, type_args), extend_def in concrete_extension_defs.items():
            self.monomorphized_extensions.append(extend_def)
            # Add to extension table for method lookup during type validation
            from sushi_lang.semantics.passes.collect import ExtensionMethod
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
            scope_analyzer = ScopeAnalyzer(unit_reporter, self.constants, self.structs, self.enums, self.generic_enums, self.generic_structs, external_table=self.externals)
            scope_analyzer.run(unit.ast)

            # Pass 2: type validation with global symbols (unit-specific reporter)
            type_validator = TypeValidator(unit_reporter, self.constants, self.structs, self.enums, self.funcs, self.extensions, self.generic_enums, self.generic_structs, self.perks, self.perk_impls, self.generic_extensions, self.generic_funcs, current_unit_name=unit.name, monomorphized_functions=monomorphizer.monomorphized_functions, external_table=self.externals)
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


    def _build_library_registry(self) -> None:
        """Build LibraryRegistry from loaded library manifests.

        Creates a LibraryRegistry with pre-parsed type information,
        eliminating duplicate parsing in codegen.
        """
        if self.library_linker is None:
            return

        from sushi_lang.backend.library_registry import LibraryRegistry
        from pathlib import Path

        self.library_registry = LibraryRegistry()

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            lib_path = Path(manifest.get("library_path", lib_name))
            self.library_registry.register_library(
                lib_path=lib_path,
                manifest=manifest,
                struct_table=self.structs.by_name if self.structs else {},
                enum_table=self.enums.by_name if self.enums else {},
            )

    def _register_library_functions(self) -> None:
        """Register functions from loaded libraries into the function table.

        Uses pre-parsed data from LibraryRegistry if available, otherwise
        falls back to manual parsing from library_linker manifests.
        """
        if self.funcs is None:
            return

        if self.library_registry is not None:
            for func_name, func_sig in self.library_registry.get_all_functions().items():
                if func_name not in self.funcs.by_name:
                    self.funcs.by_name[func_name] = func_sig
                    self.funcs.order.append(func_name)
            return

        if self.library_linker is None:
            return

        from sushi_lang.semantics.passes.collect.functions import FuncSig, Param
        from sushi_lang.semantics.type_resolution import parse_type_string

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            for func_info in manifest.get("public_functions", []):
                # Generic functions ship as instantiable templates, not concrete
                # signatures; they are registered by
                # _register_library_generic_functions instead.
                if func_info.get("is_generic"):
                    continue
                func_name = func_info["name"]
                if func_name in self.funcs.by_name:
                    continue

                params = []
                for idx, p in enumerate(func_info.get("params", [])):
                    param_type = parse_type_string(
                        p["type"],
                        self.structs.by_name if self.structs else {},
                        self.enums.by_name if self.enums else {}
                    )
                    params.append(Param(
                        name=p["name"],
                        ty=param_type,
                        name_span=None,
                        type_span=None,
                        index=idx
                    ))

                ret_type_str = func_info.get("return_type", "~")
                ret_type = parse_type_string(
                    ret_type_str,
                    self.structs.by_name if self.structs else {},
                    self.enums.by_name if self.enums else {}
                )

                func_sig = FuncSig(
                    name=func_name,
                    loc=None,
                    name_span=None,
                    ret_type=ret_type,
                    ret_span=None,
                    params=params,
                    is_public=True,
                )

                self.funcs.by_name[func_name] = func_sig
                self.funcs.order.append(func_name)

    def _seed_library_perks(self, perk_table) -> None:
        """Seed perk DEFINITIONS shipped by loaded libraries into ``perk_table``.

        Each consumed library may ship the perk contracts (method signatures)
        that its exported generics constrain on, under ``templates.perks``. We
        rebuild a ``PerkDef`` for each via the canonical collection path
        (re-parse the source snippet, run a throwaway ``CollectorPass``, pull
        the ``PerkDef`` out of the resulting ``PerkTable``) so that a consumer
        no longer has to redeclare a perk it does not author.

        This must run BEFORE the consumer's own units are collected: perk-impl
        collection (``extend T with Perk``) validates each impl against the
        visible perk definitions (CE4003), so a consumer that implements a
        library-shipped perk needs the contract present at collection time.

        Only DEFINITIONS are shipped; the consumer still supplies its own
        ``extend T with Perk`` implementations. Local definitions win: a perk is
        only seeded if its name is not already present (mirrors the other
        library-registration helpers).
        """
        if perk_table is None or self.library_linker is None:
            return

        from sushi_lang.internals.parser import parse_to_ast
        from sushi_lang.semantics.passes.collect import CollectorPass

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get("perks", []) or []:
                perk_name = record.get("name")
                if not perk_name or perk_name in perk_table.by_name:
                    continue

                source = record.get("source")
                if not source:
                    continue

                # Re-parse the self-contained perk source and run a throwaway
                # collector so any diagnostics never pollute the consumer's
                # reporter.
                program, _tree = parse_to_ast(source)
                throwaway = Reporter(source=source, filename=f"<perk:{lib_name}:{perk_name}>")
                collected = CollectorPass(throwaway).run(program, unit_name=lib_name)
                template_perks = collected[5]  # PerkTable

                perk_def = template_perks.by_name.get(perk_name)
                if perk_def is None:
                    # The snippet failed to collect as a perk; skip rather than
                    # crash the consumer build.
                    continue

                perk_table.by_name[perk_name] = perk_def
                perk_table.order.append(perk_name)

    def _register_library_generic_functions(self) -> None:
        """Register generic function templates from loaded libraries.

        Each consumed library may ship instantiable generic function bodies in
        its ``.slib`` manifest under ``templates.generic_functions``. We rebuild
        a ``GenericFuncDef`` for each via the canonical collection path
        (re-parse the source snippet, run a throwaway ``CollectorPass``, pull the
        ``GenericFuncDef`` out of the resulting table) so that the existing
        instantiation + monomorphization machinery (Pass 1.5/1.6) emits a
        concrete instance at the consumer's call site.

        Local definitions win: a template is only registered if its name is not
        already present in the generic function table (mirrors the other
        library-registration helpers).
        """
        if self.generic_funcs is None or self.library_linker is None:
            return

        from sushi_lang.internals.parser import parse_to_ast
        from sushi_lang.semantics.passes.collect import CollectorPass

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get("generic_functions", []):
                func_name = record["name"]
                if func_name in self.generic_funcs.by_name:
                    continue

                source = record.get("source")
                if not source:
                    continue

                # Re-parse the self-contained template source and run a
                # throwaway collector so any diagnostics from the library snippet
                # never pollute the consumer's reporter.
                program, _tree = parse_to_ast(source)
                throwaway = Reporter(source=source, filename=f"<template:{lib_name}:{func_name}>")
                collected = CollectorPass(throwaway).run(program, unit_name=lib_name)
                template_generic_funcs = collected[-1]  # GenericFunctionTable

                gfd = template_generic_funcs.by_name.get(func_name)
                if gfd is None:
                    # The snippet failed to collect as a generic function;
                    # skip rather than crash the consumer build.
                    continue

                gfd.is_library_template = True

                # Reconcile type-param constraints against the authoritative
                # record (the snippet already carries them, but the record is
                # the source of truth and guards against any future divergence).
                rec_tps = record.get("type_params") or []
                if len(rec_tps) == len(gfd.type_params):
                    for tp, rec_tp in zip(gfd.type_params, rec_tps):
                        if hasattr(tp, "constraints"):
                            tp.constraints = list(rec_tp.get("constraints") or [])

                self.generic_funcs.by_name[func_name] = gfd
                self.generic_funcs.order.append(func_name)

    def _register_library_structs(self) -> None:
        """Register struct definitions from loaded libraries.

        Uses pre-parsed data from LibraryRegistry if available.
        Local struct definitions take precedence over library definitions.
        """
        if self.structs is None:
            return

        if self.library_registry is not None:
            for struct_name, struct_type in self.library_registry.get_all_structs().items():
                if struct_name not in self.structs.by_name:
                    self.structs.by_name[struct_name] = struct_type
                    self.structs.order.append(struct_name)
            return

        if self.library_linker is None:
            return

        from sushi_lang.semantics.typesys import StructType
        from sushi_lang.semantics.type_resolution import parse_type_string

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            for struct_info in manifest.get("structs", []):
                struct_name = struct_info["name"]
                if struct_name in self.structs.by_name:
                    continue

                fields = []
                for f in struct_info.get("fields", []):
                    field_type = parse_type_string(
                        f["type"],
                        self.structs.by_name if self.structs else {},
                        self.enums.by_name if self.enums else {}
                    )
                    fields.append((f["name"], field_type))

                struct_type = StructType(name=struct_name, fields=tuple(fields))
                self.structs.by_name[struct_name] = struct_type
                self.structs.order.append(struct_name)

    def _register_library_enums(self) -> None:
        """Register enum definitions from loaded libraries.

        Uses pre-parsed data from LibraryRegistry if available.
        Local enum definitions take precedence over library definitions.
        """
        if self.enums is None:
            return

        if self.library_registry is not None:
            for enum_name, enum_type in self.library_registry.get_all_enums().items():
                if enum_name not in self.enums.by_name:
                    self.enums.by_name[enum_name] = enum_type
                    self.enums.order.append(enum_name)
            return

        if self.library_linker is None:
            return

        from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo
        from sushi_lang.semantics.type_resolution import parse_type_string

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            for enum_info in manifest.get("enums", []):
                enum_name = enum_info["name"]
                if enum_name in self.enums.by_name:
                    continue

                variants = []
                for v in enum_info.get("variants", []):
                    assoc_types: tuple = ()
                    if v.get("has_data") and v.get("data_type"):
                        data_type = parse_type_string(
                            v["data_type"],
                            self.structs.by_name if self.structs else {},
                            self.enums.by_name if self.enums else {}
                        )
                        assoc_types = (data_type,)

                    variants.append(EnumVariantInfo(name=v["name"], associated_types=assoc_types))

                enum_type = EnumType(name=enum_name, variants=tuple(variants))
                self.enums.by_name[enum_name] = enum_type
                self.enums.order.append(enum_name)

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
        from sushi_lang.semantics.typesys import DynamicArrayType, BuiltinType

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
