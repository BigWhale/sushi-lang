from __future__ import annotations
from typing import Dict, Optional, TYPE_CHECKING

from sushi_lang.internals.report import Origin, Reporter
from sushi_lang.semantics.ast import Program, ExtendDef, ExtendWithDef
from sushi_lang.semantics.passes.collect import CollectorPass, ConstantTable, StructTable, EnumTable, GenericEnumTable, GenericStructTable, PerkTable, PerkImplementationTable, FunctionTable, ExtensionTable, GenericExtensionTable, GenericFunctionTable

if TYPE_CHECKING:
    from sushi_lang.semantics.namespaces import NamespaceTable
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


def enum_base_names(*tables) -> set[str]:
    """The base name of every enum in `tables`, for the borrow checker's sink test."""
    names: set[str] = set()
    for table in tables:
        mapping = table.by_name if hasattr(table, 'by_name') else table
        names.update(name.split('<', 1)[0] for name in mapping)
    return names


def _diagnostic_identity(diagnostic):
    """What makes two diagnostics the same REPORT: everything the user reads first.

    A diagnostic repeated per instantiation says one thing about one line of source, so the
    second copy is noise. Notes are excluded from the identity deliberately -- two
    instantiations can attach different secondary locations to the same finding, and the
    user still wants to be told once.
    """
    span = diagnostic.span
    return (
        diagnostic.kind,
        diagnostic.code,
        diagnostic.message,
        diagnostic.filename,
        None if span is None else (span.line, span.col, span.end_line, span.end_col),
    )


class SemanticAnalyzer:
    """Semantic analysis coordinator that runs all semantic analysis passes."""

    def __init__(self, reporter: Reporter, filename: str = "<input>", unit_manager: Optional[UnitManager] = None, library_linker: Optional[object] = None, library_registry: Optional['LibraryRegistry'] = None, warn_missing_docs: bool = False,
                 generated_symbols: frozenset[str] = frozenset()) -> None:
        self.reporter = reporter
        self.filename = filename
        self.unit_manager = unit_manager
        # A backend LibraryResolver, held opaquely: semantics must not import
        # backend (Tier 4.1 layering invariant), so no tighter annotation is legal.
        self.library_linker = library_linker
        self.library_registry = library_registry
        # What the stdlib generators define, read from the manifest their build writes.
        # A NAME list and nothing else: no semantic table holds these symbols, which is
        # why CE5013 could not see them (#472).
        self.generated_symbols = generated_symbols
        # `--warn-missing-docs`. A keyword and not an options object on purpose: this is
        # the compiler's FIRST warning-control flag, and a second one is what earns the
        # object (documentation.md section 6).
        self.warn_missing_docs = warn_missing_docs
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
        self.tables: Optional['SymbolTables'] = None  # Aggregate of the above, threaded to typecheck and backend
        # What each unit may write behind a dot: one `NamespaceTable` per unit, because
        # an alias is local to the unit that wrote it (`unit-namespaces.md` section 8).
        self.namespaces: Dict[str, 'NamespaceTable'] = {}
        self.monomorphized_extensions: list['ExtendDef'] = []  # Concrete ExtendDef nodes for codegen
        self.library_perk_impls: list['ExtendWithDef'] = []  # Library-shipped impls registered here (declare-only at codegen)
        self.main_expects_args: bool = False  # Whether main function has string[] args parameter

    def check(self, program: Program) -> None:
        """Entry point for semantic analysis. Runs every pass in sequence.

        This docstring is the authority on the pass order. The passes have NAMES, not
        numbers -- a number goes out of order the moment a pass is inserted, which is how
        the old scheme ended up running the scope pass after the derive pass.

            collect       constants, headers, generic types      passes/collect/
            docs          doc blocks against their declarations  passes/docs.py
            externs       extern signatures, ptr unit gate       passes/types/externals.py
            libraries     library symbol registration            _register_library_*
            namespaces    `use ... as`, one table per unit       passes/namespaces.py
            ffi-clash     an extern naming a defined symbol      passes/types/externals.py
            entrypoint    main()'s signature                     _check_main_function_args*
            instantiate   generic instantiation collection       generics/instantiate/
            monomorphize  generic -> concrete                    generics/monomorphize/
            resolve       field and variant type resolution      passes/resolve.py
            finite-types  reject by-value containment cycles     passes/finite_types.py
            derive        auto-derived hash() and clone()        passes/derive.py
            shadowing     reject an extension over a built-in    _check_extension_shadows_builtin
            effects       destroy-effect summary                 passes/borrow/destroy_effects.py
            scope         scope and variable analysis            passes/scope.py
            typecheck     type validation and inference          passes/types/
            lift          lambda lifting                         passes/lift.py
            borrow        borrow checking                        passes/borrow/

        The last four run per unit, in one loop. `_check_monomorphized_extensions` repeats
        those four for each instantiation of a generic-target extension.

        `passes/const_eval.py` is NOT a pass: the typecheck pass and the backend both call
        it as a helper.
        """
        self._check_multi_file()

    @staticmethod
    def _unit_reporter(unit) -> Reporter:
        """A Reporter that knows one unit's file and source, for a per-unit pass."""
        try:
            source = unit.file_path.read_text(encoding="utf-8")
        except Exception:
            source = ""  # the diagnostic still renders, without its caret line
        return Reporter(source=source, filename=str(unit.file_path),
                        provenance=unit.provenance)

    def _check_multi_file(self) -> None:
        """Multi-file semantic analysis with cross-unit symbol resolution."""
        if self.unit_manager is None:
            return

        compilation_order = self.unit_manager.get_compilation_order()
        if compilation_order is None:
            return  # Error already reported

        collector = CollectorPass(
            self.reporter,
            library_units={u.name for u in compilation_order if u.provenance is not None},
        )
        from sushi_lang.semantics.tables import SymbolTables
        global_tables = SymbolTables()

        symbol_merger = SymbolTableMerger()

        # BEFORE the consumer's units: perk-impl collection validates each impl against
        # the visible perk definitions (CE4003), so the contract must already be here.
        if self.library_linker is not None:
            self._seed_library_perks(collector.perks)

        for unit in compilation_order:
            if unit.ast is None:
                continue

            unit_tables = collector.run(unit.ast, unit_name=unit.name,
                                        unit_file=str(unit.file_path))

            symbol_merger.merge_all(unit_tables, global_tables)

        # A library impl the consumer replaced must not be emitted: both bodies are
        # ordinary Sushi in ordinary units, so leaving it in place defines the method
        # symbol twice.
        shadowed = collector.perk_collector.shadowed_impls
        if shadowed:
            dropped = {id(impl) for impl in shadowed}
            for unit in compilation_order:
                if unit.ast is not None and unit.ast.perk_impls:
                    unit.ast.perk_impls = [i for i in unit.ast.perk_impls
                                           if id(i) not in dropped]

        self.tables = global_tables
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
        self.externals = collector.externals
        global_tables.externals = collector.externals

        # docs: check each doc block against the declaration beside it. Here because
        # the pass needs the merged tables and nothing later, and because it must run
        # ahead of instantiate/monomorphize -- a generic's block is written once, and
        # checking it afterwards would report one mistake once per instantiation. A
        # library unit is skipped: a consumer must not be told about the library
        # author's doc typos.
        from sushi_lang.semantics.passes.docs import check_docs, check_missing_docs
        for unit in compilation_order:
            if unit.ast is None or unit.provenance is not None:
                continue
            unit_reporter = self._unit_reporter(unit)
            check_docs(unit_reporter, unit.ast)
            # Completeness rides in the SAME loop, and nowhere later:
            # `register_synthesized_function` appends a monomorphized clone to a unit's
            # own `ast.functions`, so a lint that ran after `monomorphize` would demand
            # a doc block on every instance the program asked for.
            if self.warn_missing_docs:
                check_missing_docs(unit_reporter, unit.ast)
            self.reporter.items.extend(unit_reporter.items)

        # FFI: validate external signatures (CE5003), emit CW5001, and enforce
        # the ptr unit gate (CE5009) per unit.
        from sushi_lang.semantics.passes.types.externals import (
            validate_external_signatures, validate_ptr_unit_gate,
        )
        for unit in compilation_order:
            if unit.ast is not None:
                validate_external_signatures(self.reporter, unit.ast)
                validate_ptr_unit_gate(self.reporter, unit.ast)

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
            # And the complement: what the library declares and ships nowhere. These
            # are names, not callables, so they register in no function table (#469).
            self._register_library_not_exported()
            self._register_library_constants(compilation_order)
            self._register_library_private_types()
            # Library perk IMPLEMENTATIONS register here: after the consumer's own impls
            # (local wins) and before instantiate/monomorphize, so the constraint validator
            # sees them. The DEFINITIONS were seeded earlier, in the collect loop above.
            self._register_library_perk_impls()
            self._register_library_generic_functions()
            # Structs before enums (an enum payload may reference a struct), and both
            # before instantiate so the consumer's instantiations monomorphize locally.
            self._register_library_generic_structs()
            self._register_library_generic_enums()

        # namespaces: what each unit may write behind a dot. After `libraries`, because
        # a BINARY library's declarations exist only once that step has read the
        # manifest, and before `ffi-clash`, which is the first step that asks whether a
        # name is already taken (`unit-namespaces.md` section 3.2).
        from sushi_lang.semantics.passes.namespaces import build_namespaces
        all_units = self.unit_manager.units if self.unit_manager is not None else {}
        for unit in compilation_order:
            if unit.ast is None:
                continue
            unit_reporter = self._unit_reporter(unit)
            self.namespaces[unit.name] = build_namespaces(
                unit_reporter, unit, self.tables, units=all_units,
                library_registry=self.library_registry)
            self.reporter.items.extend(unit_reporter.items)

        # ffi-clash: an `unsafe external` may name a FOREIGN symbol, never one this
        # build defines (#470). It reads the whole program's symbols, the linked
        # libraries included, so it cannot run with the per-unit extern validation
        # above -- the registry does not exist yet up there.
        from sushi_lang.semantics.passes.types.externals import (
            reject_external_naming_a_defined_symbol,
        )
        for unit in compilation_order:
            if unit.ast is None:
                continue
            unit_reporter = self._unit_reporter(unit)
            reject_external_naming_a_defined_symbol(
                unit_reporter, unit.ast, self.tables, self.library_registry,
                self.generated_symbols)
            self.reporter.items.extend(unit_reporter.items)

        self._check_main_function_args_multi_file(compilation_order)

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
                instantiation_collector.namespaces = self.namespaces.get(unit.name)
                instantiation_collector.run(unit.ast)
        instantiation_collector.namespaces = None
        # AFTER every unit: an extension on a generic target is read per instantiation of
        # that target, and the instantiation may come from another unit (#389).
        instantiation_collector.collect_from_generic_extensions(
            [unit.ast for unit in compilation_order if unit.ast is not None]
        )
        type_instantiations = instantiation_collector.instantiations
        func_instantiations = instantiation_collector.function_instantiations

        from sushi_lang.semantics.generics.monomorphize import Monomorphizer
        from sushi_lang.semantics.generics.constraints import ConstraintValidator

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

        # Type arguments are resolved FIRST. `str(UnknownType("Point"))` and
        # `str(StructType("Point"))` are both "Point", so the two spellings mangle to one
        # enum name while carrying different payloads -- and EnumType hashes on the name but
        # compares on the variants, so the unresolved one hash-matches and compares unequal.
        # Resolving here keeps the monomorphized instance and the on-demand intern
        # byte-identical.
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

        monomorphizer.monomorphize_all_functions(func_instantiations, compilation_order)

        concrete_enums = monomorphizer.monomorphize_all(self.generic_enums.by_name, enum_instantiations)

        # A name may already be interned on demand, so keep the first entry rather than
        # clobbering it and appending a duplicate `order` key.
        for enum_name, enum_type in concrete_enums.items():
            if enum_name in self.enums.by_name:
                continue
            self.enums.by_name[enum_name] = enum_type
            self.enums.order.append(enum_name)

        concrete_structs = monomorphizer.monomorphize_all_structs(self.generic_structs.by_name, struct_instantiations)

        for struct_name, struct_type in concrete_structs.items():
            self.structs.by_name[struct_name] = struct_type
            self.structs.order.append(struct_name)

        # monomorphize (cont.): extensions on generic targets, BEFORE resolve and derive. A
        # generic call in a substituted body has its argument types only now -- they come
        # from `self` (#392) -- and what the second function round below interns must still
        # receive field resolution and hash/clone derivation. The extension TABLE merge and
        # the CE2097 check stay after derive, where their placement is load-bearing.
        concrete_extension_defs = monomorphize_all_extension_methods(
            self.generic_extensions.by_type,
            struct_instantiations,
            concrete_structs,
            enum_instantiations,
            concrete_enums,
            substitutor=monomorphizer.substitutor,
        )

        extension_fn_instantiations = set()
        for extend_def in concrete_extension_defs.values():
            extension_fn_instantiations |= monomorphizer.collect_from_extension_body(extend_def)
        if extension_fn_instantiations:
            monomorphizer.monomorphize_all_functions(extension_fn_instantiations, compilation_order)

        # resolve: AFTER monomorphization, so every struct/enum exists in the tables.
        from sushi_lang.semantics.passes.resolve import resolve_struct_field_types, resolve_enum_variant_types
        resolve_struct_field_types(self.structs, self.enums)
        resolve_enum_variant_types(self.structs, self.enums)

        # finite-types: reject types that contain themselves by value (CE2095). Must precede
        # derive, whose topological sort would report a cycle as an internal error, and must
        # stop on failure -- every later pass assumes finitely-sized types.
        from sushi_lang.semantics.passes.finite_types import check_infinite_size_types
        if check_infinite_size_types(self.structs, self.enums, self.reporter):
            return

        # derive: AFTER type resolution, and structs/enums before arrays, which may
        # contain them. The clone half carries no ordering constraint (#134).
        from sushi_lang.semantics.passes.derive import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes,
            register_all_clones,
        )
        register_all_struct_hashes(self.structs)

        register_all_enum_hashes(self.enums, self.reporter)

        register_all_array_hashes(self.structs, self.enums)

        register_all_clones(self.structs, self.enums)

        for (_target_type_name, _method_name, _type_args), extend_def in concrete_extension_defs.items():
            self.monomorphized_extensions.append(extend_def)
            # Add to extension table for method lookup during type validation.
            # The spans come along so a diagnostic about a monomorphized generic extension
            # (CE2097) can still point at the source `extend Box@(T) ...` that produced it.
            from sushi_lang.semantics.passes.collect import ExtensionMethod
            extension_method = ExtensionMethod(
                target_type=extend_def.target_type,
                name=extend_def.name,
                params=extend_def.params,
                ret_type=extend_def.ret,
                loc=getattr(extend_def, "loc", None),
                name_span=getattr(extend_def, "name_span", None),
            )
            self.extensions.add_method(extension_method)

        # An extension method colliding with a BUILT-IN can never run, because all three
        # layers resolve the built-in first -- so it is CE2097 rather than silent dead code
        # (#239). See docs/design/method-resolution.md.
        #
        # Placement is load-bearing at BOTH ends: after derive, which registers the
        # struct/enum hash/clone, and after the generic-extension merge above, which is
        # where a monomorphized `extend Box@(i32) hash()` enters the extension table.
        #
        # A perk impl is unaffected by construction: an ExtendWithDef never enters
        # ExtensionTable. It is the sanctioned way to replace a built-in.
        self._check_extension_shadows_builtin()

        # The per-unit passes run below: scope, typecheck, lift, borrow. Every unit is
        # analysed with the global context, because units reference each other, but each
        # gets its own reporter so a diagnostic names the right file.

        # effects: which functions destroy a `poke` parameter, transitively (#168).
        # Computed ONCE across EVERY unit -- the borrow pass runs per unit, so a per-unit
        # summary would make a cross-unit callee invisible.
        from sushi_lang.semantics.passes.borrow import compute_destroy_effects
        destroy_effects = compute_destroy_effects(
            unit.ast for unit in compilation_order if unit.ast is not None
        )

        # Enum type names for the borrow pass's ownership-sink test, stripped to their
        # base name: a monomorphized generic enum is interned as "Result<i32, StdError>"
        # while its constructor is written `Result.Ok(...)`.
        enum_names = enum_base_names(self.enums, self.generic_enums)

        for unit in compilation_order:
            if unit.ast is None:
                continue

            unit_reporter = self._unit_reporter(unit)

            namespaces = self.namespaces.get(unit.name)

            scope_analyzer = ScopeAnalyzer(unit_reporter, self.constants, self.structs, self.enums, self.generic_enums, self.generic_structs, external_table=self.externals,
                                           kept_constants=self._kept_constant_names(),
                                           namespaces=namespaces)
            scope_analyzer.run(unit.ast)

            type_validator = TypeValidator(
                unit_reporter, self.tables, current_unit_name=unit.name,
                monomorphized_functions=monomorphizer.monomorphized_functions,
                in_library_unit=unit.provenance is not None,
                namespaces=namespaces)
            type_validator.run(unit.ast)

            from sushi_lang.semantics.passes.lift import LambdaLifter
            LambdaLifter(self.structs, self.funcs, unit.ast,
                         annotate=type_validator._validate_function).run()

            # borrow. The enum names let the checker tell `Box.Full(a)` from a method call
            # -- both are DotCall here. BASE names only: the receiver is written bare.
            borrow_checker = BorrowChecker(unit_reporter, destroy_effects=destroy_effects,
                                           enum_names=enum_names, tables=self.tables,
                                           unit_name=unit.name,
                                           scope=namespaces.scope if namespaces else None)
            borrow_checker.run(unit.ast)

            self.reporter.items.extend(unit_reporter.items)

        if self.monomorphized_extensions:
            # The ENTRY unit, for the same reason `generics/synthesis.py` names it: a
            # lifted body belongs to the unit the compiler was pointed at, not to
            # whichever unit the compilation order happens to put first.
            lift_target = next(
                (u.ast for u in compilation_order
                 if u.ast is not None and getattr(u, "is_entry", False)),
                None)
            if lift_target is None:
                lift_target = next(
                    (u.ast for u in compilation_order if u.ast is not None), None)
            self._check_monomorphized_extensions(destroy_effects, enum_names, lift_target)

    def _check_monomorphized_extensions(self, destroy_effects, enum_names,
                                        lift_target=None) -> None:
        """Type- and borrow-check every instantiation of a generic-target extension.

        This is where a generic extension's per-instantiation truth is decided: `self` is
        concrete here, so an owning field handed out of the body is the CE2411 it is, while
        the same body over a plain type argument stays legal (#391). The template itself is
        walked by scope and borrow for what does not depend on the type argument.

        One body error would otherwise be reported once per instantiation, plus once from
        the template -- so the run collects into its own reporter and merges what is new.

        The copies mirror the per-unit order scope -> typecheck -> lift -> borrow (#399).
        They were deep-copied BEFORE scope ran, so no walk ever stamped their lambda
        captures; the scope run here exists only for that stamp and reports into a throwaway
        -- the template's own scope run already reported its findings once. The lifted functions
        land in `lift_target` (the first unit's AST) marked public, because every unit
        module DEFINES the monomorphized extension bodies and only public functions of
        another unit are declared there.
        """
        scratch = Reporter(source=self.reporter.source, filename=self.reporter.filename)
        type_validator = TypeValidator(scratch, self.tables)
        borrow_checker = BorrowChecker(scratch, destroy_effects=destroy_effects,
                                       enum_names=enum_names, tables=self.tables)

        throwaway = Reporter(source=self.reporter.source, filename=self.reporter.filename)
        capture_scope = ScopeAnalyzer(throwaway, self.constants, self.structs, self.enums,
                                      self.generic_enums, self.generic_structs,
                                      external_table=self.externals)
        capture_scope.function_names = set(self.funcs.by_name)

        lifter = None
        if lift_target is not None:
            from sushi_lang.semantics.passes.lift import LambdaLifter
            lifter = LambdaLifter(self.structs, self.funcs, lift_target,
                                  annotate=type_validator._validate_function)

        for extend_def in self.monomorphized_extensions:
            capture_scope._check_extension_method(extend_def)
            type_validator._validate_extension_method(extend_def)
            lifted = lifter.lift_body(extend_def.body) if lifter is not None else []
            for fn in lifted:
                fn.is_public = True
            borrow_checker._check_extension(extend_def)
            for fn in lifted:
                borrow_checker._check_function(fn)

        seen = {_diagnostic_identity(d) for d in self.reporter.items}
        for diagnostic in scratch.items:
            identity = _diagnostic_identity(diagnostic)
            if identity in seen:
                continue
            seen.add(identity)
            self.reporter.items.append(diagnostic)

    def _check_extension_shadows_builtin(self) -> None:
        """Reject an extension method that collides with a built-in (CE2097)."""
        if self.extensions is None:
            return

        from sushi_lang.internals import errors as er
        from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
        from sushi_lang.semantics.generics.type_display import display_type

        for target_type, methods in self.extensions.by_type.items():
            for method_name, method in methods.items():
                if not builtin_method_exists(target_type, method_name):
                    continue
                shown = f"{display_type(target_type)}.{method_name}"
                er.emit_with(
                    self.reporter, er.ERR.CE2097,
                    method.name_span or method.loc,
                    name=method_name, type=display_type(target_type),
                ).note(
                    f"'{shown}()' is defined by the compiler"
                ).help(
                    "a built-in method is always chosen before an extension method, so "
                    f"this one could never be called -- rename it, or provide "
                    f"'{method_name}()' through a perk implementation "
                    f"('extend {display_type(target_type)} with <Perk>'), which does "
                    "take precedence"
                ).emit()

    def _build_library_registry(self) -> None:
        """Build LibraryRegistry from loaded library manifests."""
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
        """Register functions from loaded libraries into the function table."""
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

        from sushi_lang.semantics.param_modes import ParamMode
        from sushi_lang.semantics.passes.collect.functions import FuncSig, Param
        from sushi_lang.semantics.type_resolution import parse_type_string

        for _lib_name, manifest in self.library_linker.loaded_libraries.items():
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
                        index=idx,
                        is_nom=p.get("mode") == ParamMode.NOM.value,
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
        """Register export-closure private helpers from loaded libraries (C4b/C5)."""
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

    def _register_library_not_exported(self) -> None:
        """Record what a loaded library declares and does not export (#469)."""
        if self.tables is None or self.library_registry is None:
            return

        self.tables.library_not_exported.update(
            self.library_registry.get_all_not_exported())

    def _kept_constant_names(self) -> frozenset[str]:
        """The constants a library declares and does not export, by name.

        The scope pass asks, so a mention reaches the type pass and hears whose the
        declaration is instead of hearing that there is none (#487).
        """
        kept = getattr(self.tables, "library_not_exported", None) or {}
        return frozenset(name for name, (_lib, kind) in kept.items()
                         if kind == "constant")

    def _register_library_constants(self, compilation_order) -> None:
        """Register a library's constants: the export closure's, and the published ones.

        Both travel as SOURCE and both are registered here, because a constant has no
        body to link: a public one is API the consumer reads (#487), a closure one is a
        private declaration a monomorphized template body names (C4b/C5). The record
        carries the `public` marker in its own text, so the visibility fence at the use
        site reads the re-parsed signature and needs nothing else.

        A clash is a different sentence for each. A published constant is a duplicate of
        the consumer's own -- the answer a source library gives for the same program
        (CE0105). A private one may not be renamed by the consumer, because the library's
        own bodies call it (CE5007).
        """
        if self.constants is None or self.library_linker is None:
            return

        host_unit = next(
            (u for u in compilation_order if u.ast is not None), None
        )
        if host_unit is None:
            return

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in manifest.get("public_constants", []) or []:
                self._register_one_library_constant(
                    host_unit, lib_name, record, published=True)
            for record in templates.get("constants", []) or []:
                self._register_one_library_constant(
                    host_unit, lib_name, record, published=False)

    def _register_one_library_constant(self, host_unit, lib_name: str, record: dict,
                                       *, published: bool) -> None:
        """Re-parse one constant record and register it under its own name."""
        import sushi_lang.internals.errors as er
        from sushi_lang.internals.parser import parse_to_ast
        from sushi_lang.semantics.passes.collect import CollectorPass

        if self.constants is None:
            return

        const_name = record.get("name")
        source = record.get("source")
        if not const_name or not source:
            # A manifest written before constants carried their source. The name and the
            # type are still printed by `--lib-info`; only the value is out of reach.
            return

        existing = self.constants.by_name.get(const_name)
        if existing is not None:
            if published:
                er.emit_with(self.reporter, er.ERR.CE0105,
                             getattr(existing, "name_span", None),
                             getattr(existing, "filename", None),
                             name=const_name) \
                    .note(f"library '{lib_name}' publishes a constant of this name") \
                    .emit()
            else:
                er.emit(self.reporter, er.ERR.CE5007,
                        getattr(existing, "name_span", None),
                        lib=lib_name, name=const_name)
            return

        program, _tree = parse_to_ast(source)
        throwaway = Reporter(
            source=source, filename=f"<const:{lib_name}:{const_name}>")
        collected = CollectorPass(throwaway).run(program, unit_name=lib_name)

        sig = collected.constants.by_name.get(const_name)
        const_defs = program.constants or []
        if sig is None or len(const_defs) != 1:
            return

        self.constants.by_name[const_name] = sig
        self.constants.order.append(const_name)
        host_unit.ast.constants.append(const_defs[0])

    def _register_library_private_types(self) -> None:
        """Register the private structs and enums the export closure ships.

        A private type is not in the manifest's `structs` / `enums` index -- the marker
        gates that -- so it travels as source beside the closure's private constants, and
        it is re-parsed here for the same reason: a monomorphized template body names it.

        The visibility table gets a PRIVATE record for each, so the consumer's own code
        cannot name it while the transplanted body can (#468).
        """
        if self.structs is None or self.enums is None or self.library_linker is None:
            return

        from sushi_lang.internals.parser import parse_to_ast
        from sushi_lang.semantics.passes.collect import CollectorPass
        from sushi_lang.semantics.visibility import DeclOrigin

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get("private_types", []) or []:
                name = record.get("name")
                source = record.get("source")
                if not name or not source:
                    continue
                if name in self.structs.by_name or name in self.enums.by_name:
                    continue

                program, _tree = parse_to_ast(source)
                throwaway = Reporter(
                    source=source, filename=f"<type:{lib_name}:{name}>")
                collected = CollectorPass(throwaway).run(program, unit_name=lib_name)

                for table, source_table in ((self.structs, collected.structs),
                                            (self.enums, collected.enums)):
                    entry = source_table.by_name.get(name)
                    if entry is None:
                        continue
                    table.by_name[name] = entry
                    table.order.append(name)

                kind = "struct" if name in self.structs.by_name else "enum"
                if self.tables is not None:
                    self.tables.visibility.record(DeclOrigin(
                        kind=kind, name=name, unit_name=lib_name, is_public=False))

    def _seed_library_perks(self, perk_table) -> None:
        """Seed perk DEFINITIONS shipped by loaded libraries into ``perk_table``."""
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
                    continue

                perk_table.by_name[perk_name] = perk_def
                perk_table.order.append(perk_name)

    def _register_library_perk_impls(self) -> None:
        """Register concrete perk IMPLEMENTATIONS shipped by loaded libraries."""
        if self.perk_impls is None or self.perks is None or self.library_linker is None:
            return

        for _lib_name, manifest in self.library_linker.loaded_libraries.items():
            templates = manifest.get("templates") or {}
            for record in templates.get("perk_impls", []) or []:
                type_name = record.get("type")
                perk_name = record.get("perk")
                if not type_name or not perk_name:
                    continue
                if perk_name not in self.perks.by_name:
                    continue
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
        """Register generic function templates from loaded libraries."""
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
                    # A local definition wins silently, but an export-closure PRIVATE
                    # template must keep its name: shadowing it would change what the
                    # library's other bodies call (CE5007).
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
                    continue

                gfd.is_library_template = True
                # Whose code the body is, and what text its spans index into. The
                # collector above already reports against the slice; the per-unit
                # passes check the MONOMORPHIZED instance and need the same answer,
                # or they render a library's mistake against the consumer's file
                # (#471). The filename shape is the throwaway reporter's, so one
                # convention names a template everywhere.
                gfd.library_origin = Origin(
                    filename=f"<template:{lib_name}:{func_name}>",
                    source=source,
                    provenance=(
                        f"'{lib_name}' {manifest.get('library_version') or 'unknown'} "
                        f"ships this template; it is monomorphized here because of "
                        f"`use <lib/{lib_name}>`"),
                )

                # The snippet already carries these, but the record is the source of
                # truth.
                rec_tps = record.get("type_params") or []
                if len(rec_tps) == len(gfd.type_params):
                    for tp, rec_tp in zip(gfd.type_params, rec_tps, strict=False):
                        if hasattr(tp, "constraints"):
                            tp.constraints = list(rec_tp.get("constraints") or [])
                        if hasattr(tp, "is_pack") and "is_pack" in rec_tp:
                            tp.is_pack = bool(rec_tp["is_pack"])

                self.generic_funcs.by_name[func_name] = gfd
                self.generic_funcs.order.append(func_name)

    def _register_library_generic_types(
        self, manifest_key: str, table, collected_attr: str
    ) -> None:
        """Register generic struct/enum templates from loaded libraries."""
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
        """Register struct definitions from loaded libraries."""
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

        for _lib_name, manifest in self.library_linker.loaded_libraries.items():
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
        """Register enum definitions from loaded libraries."""
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

        for _lib_name, manifest in self.library_linker.loaded_libraries.items():
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
        """Check if the main function has a string[] args parameter."""
        main_func = None
        for func in program.functions:
            if func.name == "main":
                main_func = func
                break

        self._process_main_function_for_args(main_func)

    def _check_main_function_args_multi_file(self, compilation_order: list[Unit]) -> None:
        """Check if the main function has a string[] args parameter in multi-file mode."""
        main_func = None
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
        """Process a main function to check if it has a string[] args parameter."""
        from sushi_lang.semantics.typesys import DynamicArrayType

        if main_func is None:
            self.main_expects_args = False
            return

        for param in main_func.params:
            if (param.name == "args" and
                isinstance(param.ty, DynamicArrayType) and
                param.ty.base_type == BuiltinType.STRING):
                self.main_expects_args = True
                return

        self.main_expects_args = False
