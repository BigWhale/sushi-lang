# semantics/semantic_analyzer.py
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import Program, ExtendDef, ExtendWithDef
from sushi_lang.semantics.passes.collect import CollectorPass, ConstantTable, StructTable, EnumTable, GenericEnumTable, GenericStructTable, PerkTable, PerkImplementationTable, FunctionTable, ExtensionTable, GenericExtensionTable, GenericFunctionTable

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.passes.scope import ScopeAnalyzer
from sushi_lang.semantics.passes.types import TypeValidator
from sushi_lang.semantics.passes.borrow import BorrowChecker
from sushi_lang.semantics.units import UnitManager, Unit
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.symbol_merger import SymbolTableMerger
from sushi_lang.semantics.generics.extensions import monomorphize_all_extension_methods
from sushi_lang.semantics.library_registry import LibraryRegistry
from sushi_lang.semantics.library_templates import deserialize_perk_impl


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

    def __init__(self, reporter: Reporter, filename: str = "<input>", unit_manager: Optional[UnitManager] = None, library_linker: Optional[object] = None, library_registry: Optional['LibraryRegistry'] = None) -> None:
        self.reporter = reporter
        self.filename = filename
        self.unit_manager = unit_manager
        # A backend LibraryResolver, held opaquely: semantics must not import
        # backend (Tier 4.1 layering invariant), so no tighter annotation is legal.
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
        self.tables: Optional['SymbolTables'] = None  # Aggregate of the above, threaded to Pass 2 and backend
        self.monomorphized_extensions: list['ExtendDef'] = []  # Concrete ExtendDef nodes for codegen
        self.library_perk_impls: list['ExtendWithDef'] = []  # Library-shipped impls registered here (declare-only at codegen)
        self.main_expects_args: bool = False  # Whether main function has string[] args parameter

    def check(self, program: Program) -> None:
        """
        Entry point for semantic analysis. Runs all semantic analysis passes in sequence.

        The production pipeline always analyzes through a UnitManager (a single
        file is a one-unit compile), so this delegates to the multi-unit path.
        """
        self._check_multi_file()

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
        from sushi_lang.semantics.tables import SymbolTables
        global_tables = SymbolTables()

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
            unit_tables = collector.run(unit.ast, unit_name=unit.name,
                                        unit_file=str(unit.file_path))

            # Merge unit symbols into global tables, considering visibility
            symbol_merger.merge_all(unit, unit_tables, global_tables)

        self.tables = global_tables
        # Individual attributes are views onto the same table objects; the
        # pipeline and backend read analyzer.structs / .enums / ... directly.
        self.constants = global_tables.constants
        self.structs = global_tables.structs
        self.enums = global_tables.enums
        self.generic_enums = global_tables.generic_enums
        self.generic_structs = global_tables.generic_structs
        self.perks = global_tables.perks
        self.perk_impls = global_tables.perk_impls
        self.funcs = global_tables.funcs
        self.extensions = global_tables.extensions
        self.generic_extensions = global_tables.generic_extensions
        self.generic_funcs = global_tables.generic_funcs
        # FFI externals accumulate across all units in the shared collector table.
        self.externals = collector.externals
        global_tables.externals = collector.externals

        # FFI: validate external signatures (CE5003), emit CW5001, and enforce
        # the ptr unit gate (CE5009) per unit.
        from sushi_lang.semantics.passes.types.externals import (
            validate_external_signatures, validate_ptr_unit_gate,
        )
        for unit in compilation_order:
            if unit.ast is not None:
                validate_external_signatures(self.reporter, unit.ast)
                validate_ptr_unit_gate(self.reporter, unit.ast)

        # Register library types if any libraries are loaded
        # Order: structs first, then enums (may reference structs), then functions (may reference both)
        if self.library_linker is not None and self.library_registry is None:
            self._build_library_registry()
        if self.library_registry is not None or self.library_linker is not None:
            self._register_library_structs()
            self._register_library_enums()
            self._register_library_functions()
            # Export-closure private helpers and constants (C4b/C5): clash
            # with a local name is CE5007 (local-wins would silently change
            # what the library's monomorphized bodies call).
            self._register_library_private_functions()
            self._register_library_constants(compilation_order)
            # NOTE: shipped library perk DEFINITIONS are seeded earlier, before
            # the consumer's perk-impl collection (see _seed_library_perks call
            # in the Phase 0 loop above); they are already in self.perks here.
            # Library perk IMPLEMENTATIONS register here, after the consumer's
            # own impls (local wins) and before Pass 1.5/1.6 so the constraint
            # validator sees them at monomorphization.
            self._register_library_perk_impls()
            self._register_library_generic_functions()
            # Generic struct/enum templates: structs first (enum payloads may
            # reference structs), then enums. Registered before Pass 1.5 so the
            # consumer's instantiations of LibBox<i32> etc. are collected and
            # monomorphized locally.
            self._register_library_generic_structs()
            self._register_library_generic_enums()

        # Check if main function expects command line arguments (across all units)
        self._check_main_function_args_multi_file(compilation_order)

        # Pass 1.5: collect generic type instantiations from all units
        from sushi_lang.semantics.generics.instantiate import InstantiationCollector
        instantiation_collector = InstantiationCollector(
            struct_table=self.structs.by_name,
            enum_table=self.enums.by_name,
            generic_structs=self.generic_structs.by_name,
            generic_funcs=self.generic_funcs.by_name,
            func_table=self.funcs.by_name,
            tables=self.tables,
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
            constraint_validator=constraint_validator,
            generic_funcs=self.generic_funcs.by_name,
            generic_enums=self.generic_enums.by_name,
            generic_structs=self.generic_structs.by_name,
            func_table=self.funcs,
            enum_table=self.enums,
            struct_table=self.structs,
            tables=self.tables,
        )

        # Separate enum and struct instantiations.
        #
        # Type arguments are resolved FIRST. A collected instantiation can name a user type as a
        # bare UnknownType (e.g. Result<Point, StdError> arrives as UnknownType("Point")), and the
        # monomorphizer builds its concrete EnumType directly -- it does not go through
        # ensure_*_type_in_table, so nothing else would resolve it. Since `str(UnknownType("Point"))`
        # and `str(StructType("Point"))` are both "Point", the two mangle to the SAME enum name
        # while carrying different payloads: EnumType hashes on the name but compares on the
        # variants, so the unresolved one hash-matches and compares unequal. Resolving here keeps
        # the monomorphized instance and the on-demand intern byte-identical.
        # (Abstract instantiations are dropped by the monomorphizer itself, which is the one
        # choke point every source of instantiations flows through.)
        from sushi_lang.semantics.type_resolution import resolve_unknown_type

        def _resolve_args(type_args):
            return tuple(
                resolve_unknown_type(arg, self.structs.by_name, self.enums.by_name)
                for arg in type_args
            )

        enum_instantiations = set()
        struct_instantiations = set()
        for base_name, type_args in type_instantiations:
            if base_name in self.generic_enums.by_name:
                enum_instantiations.add((base_name, _resolve_args(type_args)))
            elif base_name in self.generic_structs.by_name:
                struct_instantiations.add((base_name, _resolve_args(type_args)))

        # Phase 3: Monomorphize generic functions
        # Monomorphize all detected function instantiations (multi-file mode)
        monomorphizer.monomorphize_all_functions(func_instantiations, compilation_order)

        # Monomorphize generic enums
        concrete_enums = monomorphizer.monomorphize_all(self.generic_enums.by_name, enum_instantiations)

        # Merge monomorphized concrete enums into the global enum table. A name may already be
        # interned on demand (ensure_result_type_in_table / ensure_maybe_type_in_table run during
        # Pass 2 and codegen), so keep the first entry rather than clobbering it and appending a
        # duplicate `order` key -- the two paths mangle the same name from the same type args.
        for enum_name, enum_type in concrete_enums.items():
            if enum_name in self.enums.by_name:
                continue
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

        # Destroy-effect summary for Pass 3 (#168): which functions destroy a `&poke`
        # parameter, transitively. Computed ONCE across EVERY unit -- the borrow checker
        # runs per unit, so a per-unit summary would make a cross-unit callee invisible.
        from sushi_lang.semantics.passes.borrow import compute_destroy_effects
        destroy_effects = compute_destroy_effects(
            unit.ast for unit in compilation_order if unit.ast is not None
        )

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
            type_validator = TypeValidator(unit_reporter, self.tables, current_unit_name=unit.name, monomorphized_functions=monomorphizer.monomorphized_functions)
            type_validator.run(unit.ast)

            # Pass 2.5: lambda-lifting (between type and borrow, per unit).
            from sushi_lang.semantics.passes.lambda_lift import LambdaLifter
            LambdaLifter(self.structs, self.funcs, unit.ast,
                         annotate=type_validator._validate_function).run()

            # Pass 3: borrow checking (unit-specific reporter)
            borrow_checker = BorrowChecker(unit_reporter, destroy_effects=destroy_effects)
            borrow_checker.run(unit.ast)

            # Merge unit reporter results into main reporter
            self.reporter.items.extend(unit_reporter.items)

        # Validate monomorphized generic extension methods (use main reporter for now)
        if self.monomorphized_extensions:
            # Create a type validator with global context
            type_validator = TypeValidator(self.reporter, self.tables)
            for extend_def in self.monomorphized_extensions:
                type_validator._validate_extension_method(extend_def)


    def _build_library_registry(self) -> None:
        """Build LibraryRegistry from loaded library manifests.

        Creates a LibraryRegistry with pre-parsed type information,
        eliminating duplicate parsing in codegen.
        """
        if self.library_linker is None:
            return

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

    def _register_library_private_functions(self) -> None:
        """Register export-closure private helpers from loaded libraries (C4b/C5).

        A library generic's body may call library-private concrete helpers;
        these ship as signature-only records (``templates.private_functions``)
        and their definitions link from the library bitcode. Registering the
        signature here lets the consumer's type checker validate the
        monomorphized body's call sites.

        Unlike every other registration helper, a name clash is an ERROR
        (CE5007), not local-wins: the library's monomorphized body calls the
        symbol by name, so a local function shadowing it would silently change
        the library's behavior.
        """
        if self.funcs is None or self.library_registry is None:
            return

        import sushi_lang.internals.errors as er

        for name, (lib_name, sig) in self.library_registry.get_all_private_functions().items():
            existing = self.funcs.by_name.get(name)
            if existing is not None:
                er.emit(self.reporter, er.ERR.CE5007,
                        getattr(existing, "name_span", None),
                        lib=lib_name, name=name)
                continue
            self.funcs.by_name[name] = sig
            self.funcs.order.append(name)

    def _register_library_constants(self, compilation_order) -> None:
        """Register export-closure constants from loaded libraries (C4b/C5).

        Shipped with their source (``templates.constants``) because the
        consumer needs the VALUE for compile-time evaluation. Each record is
        re-parsed; its signature merges into the constant table and its
        ``ConstDef`` is appended to the first unit's AST so both codegen paths
        emit the global (constants are emitted with internal linkage per
        module, so re-emission alongside the library bitcode cannot collide).

        Name clashes are CE5007, same rationale as private functions.
        """
        if self.constants is None or self.library_linker is None:
            return

        import sushi_lang.internals.errors as er
        from sushi_lang.internals.parser import parse_to_ast
        from sushi_lang.semantics.passes.collect import CollectorPass

        host_unit = next(
            (u for u in compilation_order if u.ast is not None), None
        )
        if host_unit is None:
            return

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get("constants", []) or []:
                const_name = record.get("name")
                source = record.get("source")
                if not const_name or not source:
                    continue

                existing = self.constants.by_name.get(const_name)
                if existing is not None:
                    er.emit(self.reporter, er.ERR.CE5007,
                            getattr(existing, "name_span", None),
                            lib=lib_name, name=const_name)
                    continue

                program, _tree = parse_to_ast(source)
                throwaway = Reporter(
                    source=source, filename=f"<const:{lib_name}:{const_name}>")
                collected = CollectorPass(throwaway).run(program, unit_name=lib_name)
                const_table = collected.constants

                sig = const_table.by_name.get(const_name)
                const_defs = program.constants or []
                if sig is None or len(const_defs) != 1:
                    # The snippet failed to collect as a constant; skip rather
                    # than crash the consumer build.
                    continue

                self.constants.by_name[const_name] = sig
                self.constants.order.append(const_name)
                host_unit.ast.constants.append(const_defs[0])

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
                template_perks = collected.perks

                perk_def = template_perks.by_name.get(perk_name)
                if perk_def is None:
                    # The snippet failed to collect as a perk; skip rather than
                    # crash the consumer build.
                    continue

                perk_table.by_name[perk_name] = perk_def
                perk_table.order.append(perk_name)

    def _register_library_perk_impls(self) -> None:
        """Register concrete perk IMPLEMENTATIONS shipped by loaded libraries.

        C4a: each library may ship its own concrete ``extend T with Perk``
        blocks for the perks its exported generics constrain on, under
        ``templates.perk_impls``. Registering them in the consumer's perk-impl
        table makes the constraint validator (CE4006) and method dispatch see
        the impl; the bodies are NOT re-emitted - codegen declares the method
        symbols and the definitions resolve from the library bitcode at link
        time (they carry weak linkage there, so a local impl wins).

        Precedence (all silent, mirroring the other registration helpers):
        - A consumer's own impl of the same (type, perk) wins outright.
        - Across multiple libraries shipping the same impl, the first
          registered wins.
        - If a local extension method on the target type already uses one of
          the impl's method names, the library impl is skipped entirely:
          registering it would create exactly the dispatch ambiguity CE4007
          exists to prevent, but erroring would make adding an impl to a
          library a breaking change for consumers. (If the consumer then needs
          the perk, writing its own ``extend`` triggers the normal in-program
          CE4007 with a proper source span.)
        """
        if self.perk_impls is None or self.perks is None or self.library_linker is None:
            return

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get("perk_impls", []) or []:
                type_name = record.get("type")
                perk_name = record.get("perk")
                if not type_name or not perk_name:
                    continue
                # Defensive: the shipping rule guarantees the perk definition
                # ships alongside, but a hand-edited manifest may not honor it.
                if perk_name not in self.perks.by_name:
                    continue
                # Local (or earlier-library) impl wins.
                if self.perk_impls.implements(type_name, perk_name):
                    continue
                # CE4007 interplay: skip on a method-name clash with a local
                # extension method on the same type.
                existing_methods = (
                    self.extensions.by_type.get(type_name, {})
                    if self.extensions is not None else {}
                )
                method_names = [
                    m.get("name") for m in record.get("methods", []) or []
                ]
                if any(name in existing_methods for name in method_names):
                    continue

                try:
                    impl = deserialize_perk_impl(record)
                except Exception:
                    # The snippet failed to re-parse; skip rather than crash the
                    # consumer build (it can supply its own impl) -- but say so, or the
                    # user later gets "no such method" on a perk the library implements.
                    from sushi_lang.internals import errors as er
                    er.emit(self.reporter, er.ERR.CW3506, None, type=type_name)
                    continue

                if self.perk_impls.register(impl, type_name):
                    self.library_perk_impls.append(impl)

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

        import sushi_lang.internals.errors as er

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get("generic_functions", []):
                func_name = record["name"]
                if func_name in self.generic_funcs.by_name:
                    # Public templates: local definitions win silently. An
                    # export-closure PRIVATE template (C4b/C5) must keep its
                    # name - shadowing it would change what the library's
                    # other shipped bodies call (CE5007).
                    if record.get("private"):
                        existing = self.generic_funcs.by_name[func_name]
                        er.emit(self.reporter, er.ERR.CE5007,
                                getattr(existing, "name_span", None),
                                lib=lib_name, name=func_name)
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
                template_generic_funcs = collected.generic_funcs

                gfd = template_generic_funcs.by_name.get(func_name)
                if gfd is None:
                    # The snippet failed to collect as a generic function;
                    # skip rather than crash the consumer build.
                    continue

                gfd.is_library_template = True

                # Reconcile type-param constraints and the type-pack marker
                # against the authoritative record (the snippet already carries
                # them, but the record is the source of truth and guards against
                # any future divergence).
                rec_tps = record.get("type_params") or []
                if len(rec_tps) == len(gfd.type_params):
                    for tp, rec_tp in zip(gfd.type_params, rec_tps):
                        if hasattr(tp, "constraints"):
                            tp.constraints = list(rec_tp.get("constraints") or [])
                        if hasattr(tp, "is_pack") and "is_pack" in rec_tp:
                            tp.is_pack = bool(rec_tp["is_pack"])

                self.generic_funcs.by_name[func_name] = gfd
                self.generic_funcs.order.append(func_name)

    def _register_library_generic_types(
        self, manifest_key: str, table, collected_attr: str
    ) -> None:
        """Register generic struct/enum templates from loaded libraries.

        Shared by ``_register_library_generic_structs`` /
        ``_register_library_generic_enums``. Mirrors
        ``_register_library_generic_functions``: each consumed library may ship
        instantiable generic struct/enum templates under
        ``templates.generic_structs`` / ``templates.generic_enums``. We rebuild a
        ``GenericStructType`` / ``GenericEnumType`` for each via the canonical
        collection path (re-parse the source snippet, run a throwaway
        ``CollectorPass``, pull the generic type out of the resulting table) so
        the existing instantiation + monomorphization machinery (Pass 1.5/1.6)
        emits a concrete instance at the consumer's call site.

        Local definitions win: a template is only registered if its name is not
        already present in the target table.

        Args:
            manifest_key: ``"generic_structs"`` or ``"generic_enums"``.
            table: the consumer's ``GenericStructTable`` / ``GenericEnumTable``.
            collected_attr: attribute of the ``SymbolTables`` result holding the
                matching table (``"generic_structs"`` or ``"generic_enums"``).
        """
        if table is None or self.library_linker is None:
            return

        from sushi_lang.internals.parser import parse_to_ast
        from sushi_lang.semantics.passes.collect import CollectorPass

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get(manifest_key, []):
                type_name = record["name"]
                if type_name in table.by_name:
                    continue

                source = record.get("source")
                if not source:
                    continue

                program, _tree = parse_to_ast(source)
                throwaway = Reporter(source=source, filename=f"<template:{lib_name}:{type_name}>")
                collected = CollectorPass(throwaway).run(program, unit_name=lib_name)
                template_table = getattr(collected, collected_attr)

                generic_type = template_table.by_name.get(type_name)
                if generic_type is None:
                    # The snippet failed to collect as a generic type; skip
                    # rather than crash the consumer build.
                    continue

                table.by_name[type_name] = generic_type
                table.order.append(type_name)

    def _register_library_generic_structs(self) -> None:
        """Register generic struct templates from loaded libraries (index 4)."""
        self._register_library_generic_types(
            "generic_structs", self.generic_structs, "generic_structs")

    def _register_library_generic_enums(self) -> None:
        """Register generic enum templates from loaded libraries (index 3)."""
        self._register_library_generic_types(
            "generic_enums", self.generic_enums, "generic_enums")

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
        from sushi_lang.semantics.typesys import DynamicArrayType

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
