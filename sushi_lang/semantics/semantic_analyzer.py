from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals.report import (
    Reporter, diagnostic_identity, in_source_order)
from sushi_lang.semantics.ast import ExtendDef, ExtendWithDef
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.tables import SymbolTables

if TYPE_CHECKING:
    from sushi_lang.semantics.library_registry import LibraryRegistry
from sushi_lang.semantics.passes.scope import ScopeAnalyzer
from sushi_lang.semantics.passes.types import TypeValidator
from sushi_lang.semantics.passes.borrow import BorrowChecker
from sushi_lang.semantics.units import UnitManager, Unit
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.generics.extensions import monomorphize_all_extension_methods
from sushi_lang.semantics.library_registration import (
    LibraryRegistration, LoadedLibraries)


def enum_base_names(*tables) -> set[str]:
    """The base name of every enum in `tables`, for the borrow checker's sink test."""
    names: set[str] = set()
    for table in tables:
        mapping = table.by_name if hasattr(table, 'by_name') else table
        names.update(name.split('<', 1)[0] for name in mapping)
    return names


class SemanticAnalyzer:
    """Semantic analysis coordinator that runs all semantic analysis passes."""

    def __init__(self, reporter: Reporter, filename: str = "<input>", unit_manager: Optional[UnitManager] = None, library_linker: Optional[LoadedLibraries] = None, library_registry: Optional['LibraryRegistry'] = None, warn_missing_docs: bool = False,
                 generated_symbols: frozenset[str] = frozenset(),
                 is_library: bool = False) -> None:
        self.reporter = reporter
        self.filename = filename
        self.unit_manager = unit_manager
        # A backend LibraryResolver, held through a Protocol: semantics must not
        # import backend (Tier 4.1 layering invariant).
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
        # `--lib`. The `entrypoint` pass is the one home of main's rule and the rule
        # turns on the build kind: an executable needs a main, a library refuses one.
        self.is_library = is_library
        # The whole program's symbol tables, and the ONE home of each of them: the
        # `namespaces` a unit may write behind a dot are `tables.namespaces`, and so on
        # for the other eighteen. Empty until the collect loop replaces them with what
        # the collect pass filled -- never None, so no reader needs a guard (#673).
        self.tables: SymbolTables = SymbolTables()
        self.monomorphized_extensions: list['ExtendDef'] = []  # Concrete ExtendDef nodes for codegen
        self.library_perk_impls: list['ExtendWithDef'] = []  # Library-shipped impls registered here (declare-only at codegen)
        self.libraries: Optional[LibraryRegistration] = None  # The `libraries` step, for its two later readers
        self.main_expects_args: bool = False  # Whether main function has string[] args parameter

    def check(self) -> None:
        """Entry point for semantic analysis. Runs every pass in sequence.

        This docstring is the authority on the pass order. The passes have NAMES, not
        numbers -- a number goes out of order the moment a pass is inserted, which is how
        the old scheme ended up running the scope pass after the derive pass.

            collect       constants, headers, generic types      passes/collect/
            docs          doc blocks against their declarations  passes/docs.py
            externs       extern signatures, ptr unit gate       passes/types/externals.py
            libraries     library symbol registration            library_registration.py
            namespaces    `use ... as`, one table per unit       passes/namespaces.py
            ffi-clash     an extern naming a defined symbol      passes/types/externals.py
            entrypoint    main(): it exists, returns i32        _check_entrypoint
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

        `semantics/const_eval.py` is NOT a pass. THREE callers reach it as a helper: the
        AST BUILDER, which reads a fixed array's size while the unit is parsed (Known
        Limitation 12) and keeps a constant table of its own for it; the `typecheck`
        pass; and the backend. The last two share the collect pass's table, and with it
        the fold memo (#597).
        """
        self._check_multi_file()

    @staticmethod
    def _unit_reporter(unit) -> Reporter:
        """A Reporter that knows one unit's file and source, for a per-unit pass."""
        return Reporter(source=unit.read_source(), filename=str(unit.file_path),
                        provenance=unit.provenance)

    def _merge_unit(self, unit_reporter: Reporter) -> None:
        """Hand one unit's findings to the program reporter, in source order.

        The one place a per-unit reporter is drained. The passes emit in pass order and
        a unit is walked whole by each of them, so a fault the `lift` pass found sits
        behind every fault the `typecheck` pass found -- source order is what a reader
        asked for (#629).
        """
        self.reporter.items.extend(in_source_order(unit_reporter.items))

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
        # The collect pass fills ONE set of tables, in place, once per unit -- so what
        # it holds IS the whole program's answer and nothing copies it into a second
        # set (#672).
        global_tables = collector.tables

        libraries = LibraryRegistration(self.reporter, global_tables,
                                        self.library_linker, self.library_registry)
        self.libraries = libraries
        # BEFORE the consumer's units: perk-impl collection validates each impl against
        # the visible perk definitions (CE4003), so the contract must already be here.
        if self.library_linker is not None:
            libraries.seed_perks(global_tables.perks)

        for unit in compilation_order:
            if unit.ast is None:
                continue

            collector.run(unit.ast, unit_name=unit.name,
                          unit_file=str(unit.file_path))

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

        # docs: check each doc block against the declaration beside it. Here because
        # the pass needs the collected tables and nothing later, and because it must run
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
            self._merge_unit(unit_reporter)

        # FFI: validate external signatures (CE5003), emit CW5001, and enforce
        # the ptr unit gate (CE5009) per unit.
        from sushi_lang.semantics.passes.types.externals import (
            validate_external_signatures, validate_ptr_unit_gate,
        )
        for unit in compilation_order:
            if unit.ast is None:
                continue
            unit_reporter = self._unit_reporter(unit)
            validate_external_signatures(unit_reporter, unit.ast)
            validate_ptr_unit_gate(unit_reporter, unit.ast)
            self._merge_unit(unit_reporter)

        # libraries: every symbol a binary `.slib` exports enters the tables filled
        # above. The order of the arms is the step's own (`library_registration.py`);
        # the perk DEFINITIONS were seeded ahead of the collect loop.
        libraries.register(compilation_order)
        self.library_registry = libraries.registry
        self.library_perk_impls = libraries.shipped_perk_impls

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
            self.tables.namespaces[unit.name] = build_namespaces(
                unit_reporter, unit, self.tables, units=all_units,
                library_registry=self.library_registry)
            self._merge_unit(unit_reporter)

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
            self._merge_unit(unit_reporter)

        self._check_entrypoint(compilation_order)

        from sushi_lang.semantics.generics.instantiate import InstantiationCollector
        instantiation_collector = InstantiationCollector(
            struct_table=self.tables.structs.by_name,
            enum_table=self.tables.enums.by_name,
            generic_structs=self.tables.generic_structs.by_name,
            generic_funcs=self.tables.generic_funcs.by_name,
            func_table=self.tables.funcs.by_name,
            tables=self.tables,
        )
        for unit in compilation_order:
            if unit.ast is not None:
                instantiation_collector.namespaces = self.tables.namespaces.get(unit.name)
                instantiation_collector.current_file = (
                    str(unit.file_path) if getattr(unit, "file_path", None) else None)
                # The collector resolves a generic the way the unit's own body does:
                # its own declaration first, then what its imports brought (#495).
                unit_scope = getattr(instantiation_collector.namespaces, "scope", None)
                instantiation_collector.generic_funcs = self.tables.generic_funcs.view_for(
                    unit.name, unit_scope)
                instantiation_collector.run(unit.ast)
        instantiation_collector.namespaces = None
        instantiation_collector.current_file = None
        instantiation_collector.generic_funcs = self.tables.generic_funcs.by_name
        # A BINARY library's signatures name instantiations too, and no unit walk sees
        # them (#543): `fn make_box(i32 v) Box@(i32)` is a manifest record here.
        if self.library_registry is not None:
            instantiation_collector.collect_from_signatures(libraries.signatures())
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
            perk_table=self.tables.perks,
            perk_impl_table=self.tables.perk_impls,
            reporter=self.reporter,
            generic_perk_impls=self.tables.generic_perk_impls,
            struct_table=self.tables.structs,
            enum_table=self.tables.enums,
        )

        monomorphizer = Monomorphizer(
            reporter=self.reporter,
            constraint_validator=constraint_validator,
            generic_funcs=self.tables.generic_funcs,
            generic_enums=self.tables.generic_enums.by_name,
            generic_structs=self.tables.generic_structs.by_name,
            func_table=self.tables.funcs,
            enum_table=self.tables.enums,
            struct_table=self.tables.structs,
            tables=self.tables,
            sites=instantiation_collector.sites,
        )

        # The late-interning seam (risk 1 of the UFCS epic): when the per-unit
        # typecheck solves a method-level type argument, the substituted signature can
        # name an instantiation nothing else names -- reachable from the pass through
        # the tables, because the pass has no monomorphizer of its own.
        self.tables.intern_generic_ref = (
            lambda ty: self._intern_generic_type_refs(monomorphizer, (ty,)))

        # Type arguments are resolved FIRST. `str(UnknownType("Point"))` and
        # `str(StructType("Point"))` are both "Point", so the two spellings mangle to one
        # enum name while carrying different payloads -- and EnumType hashes on the name but
        # compares on the variants, so the unresolved one hash-matches and compares unequal.
        # Resolving here keeps the monomorphized instance and the on-demand intern
        # byte-identical.
        from sushi_lang.semantics.type_resolution import resolve_unknown_type

        def _resolve_args(type_args):
            return tuple(
                resolve_unknown_type(arg, self.tables.structs.by_name, self.tables.enums.by_name)
                for arg in type_args
            )

        enum_instantiations = set()
        struct_instantiations = set()
        for base_name, type_args in type_instantiations:
            if base_name in self.tables.generic_enums.by_name:
                enum_instantiations.add((base_name, _resolve_args(type_args)))
            elif base_name in self.tables.generic_structs.by_name:
                struct_instantiations.add((base_name, _resolve_args(type_args)))

        # Both publish into the tables at creation (`TypeMonomorphizer._publish`), the
        # nested instances included.
        concrete_enums = monomorphizer.monomorphize_all(self.tables.generic_enums.by_name, enum_instantiations)
        concrete_structs = monomorphizer.monomorphize_all_structs(self.tables.generic_structs.by_name, struct_instantiations)

        # The worklist (#577): an instance a substitution REACHED -- `Box<string>` from a
        # `Box@(B)` field of `Pair<i32, string>`, `Maybe<string>` from a payload -- is an
        # instantiation like a spelled one, so the extension and perk copies below are cut
        # for it in this round. Keyed by interned name: a spelled instantiation may carry
        # the same name under another argument spelling, and one copy per name is the rule.
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        reached_enums, reached_structs = monomorphizer.reached_instances()
        for instantiations, concrete, reached in (
                (enum_instantiations, concrete_enums, reached_enums),
                (struct_instantiations, concrete_structs, reached_structs)):
            named = {instantiation_key(base, args) for base, args in instantiations}
            for (base, args), ty in reached.items():
                if ty.name in named:
                    continue
                named.add(ty.name)
                instantiations.add((base, args))
                concrete[ty.name] = ty

        # A perk implementation on a GENERIC target is instantiated BEFORE the functions
        # are, and that order is load-bearing: a `@(S: Show)` constraint is checked while
        # a generic function is monomorphized, so `Box@(i32)` has to already say it
        # implements `Show` or the call is CE4006 for a type that does.
        perk_impl_fn_instantiations: set = set()
        self._monomorphize_generic_perk_impls(
            monomorphizer, compilation_order, struct_instantiations, concrete_structs,
            enum_instantiations, concrete_enums, perk_impl_fn_instantiations)

        monomorphizer.monomorphize_all_functions(func_instantiations, compilation_order)

        # monomorphize (cont.): extensions on generic targets, BEFORE resolve and derive. A
        # generic call in a substituted body has its argument types only now -- they come
        # from `self` (#392) -- and what the second function round below interns must still
        # receive field resolution and hash/clone derivation. The extension TABLE merge and
        # the CE2097 check stay after derive, where their placement is load-bearing.
        concrete_extension_defs = monomorphize_all_extension_methods(
            self.tables.generic_extensions.by_type,
            struct_instantiations,
            concrete_structs,
            enum_instantiations,
            concrete_enums,
            substitutor=monomorphizer.substitutor,
        )

        extension_fn_instantiations = set(perk_impl_fn_instantiations)
        for extend_def in concrete_extension_defs.values():
            extension_fn_instantiations |= monomorphizer.collect_from_extension_body(extend_def)

        if extension_fn_instantiations:
            monomorphizer.monomorphize_all_functions(extension_fn_instantiations, compilation_order)

        # monomorphize (cont.): a LATE instantiation -- one the instantiate pass never
        # saw, interned while a generic body was substituted -- gets its generic-target
        # extension and perk-implementation copies exactly as an early one did (#555).
        self._cut_templates_for_late_instantiations(
            monomorphizer, compilation_order, concrete_extension_defs,
            struct_instantiations, enum_instantiations)

        # A constraint violation STOPS the whole-program analysis here (#579, Ruling 4),
        # as CE2095 does below. CE4006 stands at the type that named the refused
        # instantiation, no copy was cut for it, and the per-unit passes would only
        # read the same fault back as a CE2008 from inside a template body.
        if monomorphizer.constraint_violations:
            return

        # resolve: AFTER monomorphization, so every struct/enum exists in the tables.
        from sushi_lang.semantics.passes.resolve import (
            resolve_constant_types, resolve_struct_field_types, resolve_enum_variant_types)
        resolve_struct_field_types(self.tables.structs, self.tables.enums)
        resolve_enum_variant_types(self.tables.structs, self.tables.enums)
        resolve_constant_types(self.tables.constants, self.tables.structs, self.tables.enums)

        # finite-types: reject types that contain themselves by value (CE2095), and stop
        # on failure -- every later pass assumes finitely-sized types.
        from sushi_lang.semantics.passes.finite_types import check_infinite_size_types
        if check_infinite_size_types(self.tables.structs, self.tables.enums, self.reporter):
            return

        # derive: AFTER type resolution, and structs/enums before arrays, which may
        # contain them. The clone half carries no ordering constraint (#134).
        from sushi_lang.semantics.passes.derive import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes,
            register_all_clones,
        )
        register_all_struct_hashes(self.tables.structs, self.tables.derived_methods)

        register_all_enum_hashes(self.tables.enums, self.tables.derived_methods)

        register_all_array_hashes(self.tables.structs, self.tables.enums, self.tables.derived_methods)

        register_all_clones(self.tables.structs, self.tables.enums, self.tables.derived_methods)

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
                err_type=getattr(extend_def, "err_type", None),
                err_span=getattr(extend_def, "err_span", None),
                # The receiver's MODE, because the CALL SITE reads it off this record:
                # a `poke self` receiver arrives by pointer and a `nom self` one is
                # consumed. Dropped here, every generic-target method's receiver was a
                # private copy -- a write was lost (#253's shape) and a consuming
                # receiver was freed twice, in the method and again at the call site.
                self_mode=getattr(extend_def, "self_mode", None),
                # And whether there is a receiver at all (#542): without it the call
                # site resolves a static as an instance method and CE2102 refuses the
                # very declaration that answers it.
                is_static=getattr(extend_def, "is_static", False),
            )
            self.tables.extensions.add_method(extension_method)

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
        enum_names = enum_base_names(self.tables.enums, self.tables.generic_enums)

        for unit in compilation_order:
            if unit.ast is None:
                continue

            unit_reporter = self._unit_reporter(unit)

            namespaces = self.tables.namespaces.get(unit.name)

            scope_analyzer = ScopeAnalyzer(unit_reporter, self.tables.constants, self.tables.structs, self.tables.enums, self.tables.generic_enums, self.tables.generic_structs, external_table=self.tables.externals,
                                           kept_constants=libraries.kept_constant_names(),
                                           namespaces=namespaces)
            scope_analyzer.run(unit.ast)

            type_validator = TypeValidator(
                unit_reporter, self.tables, current_unit_name=unit.name,
                monomorphized_functions=monomorphizer.monomorphized_functions,
                in_library_unit=unit.provenance is not None,
                namespaces=namespaces)
            type_validator.run(unit.ast)

            from sushi_lang.semantics.passes.lift import LambdaLifter
            LambdaLifter(self.tables.structs, self.tables.funcs, unit.ast,
                         annotate=type_validator).run()

            # borrow. The enum names let the checker tell `Box.Full(a)` from a method call
            # -- both are DotCall here. BASE names only: the receiver is written bare.
            borrow_checker = BorrowChecker(unit_reporter, destroy_effects=destroy_effects,
                                           enum_names=enum_names, tables=self.tables,
                                           unit_name=unit.name,
                                           scope=namespaces.scope if namespaces else None)
            borrow_checker.run(unit.ast)

            self._merge_unit(unit_reporter)

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

        # Fixpoint: an array-target template instantiates at the CALL SITE (the
        # typecheck pass queued it), and checking one monomorphized body can resolve a
        # call that queues another. The bound mirrors MAX_EXPANSION_ROUNDS in the
        # instantiate pass: reaching it drops an instantiation, which surfaces as the
        # ordinary CE2008, never as a hang.
        checked = 0
        for _round in range(self.MAX_ARRAY_EXPANSION_ROUNDS):
            self._drain_pending_array_extensions(monomorphizer, compilation_order)
            batch = self.monomorphized_extensions[checked:]
            if not batch:
                break
            checked = len(self.monomorphized_extensions)
            self._check_monomorphized_extensions(destroy_effects, enum_names,
                                                 lift_target, only=batch)

    # Rounds the call-site-driven fixpoint may take before it drops the rest. The
    # same shape and reasoning as InstantiationCollector.MAX_EXPANSION_ROUNDS.
    MAX_ARRAY_EXPANSION_ROUNDS = 8

    def _drain_pending_array_extensions(self, monomorphizer, compilation_order) -> None:
        """Monomorphize every queued call-site extension instantiation.

        The typecheck pass queued (template, target, receiver args, method args) while
        it resolved calls -- an array template's element (ruling 3) or a
        method-generic's solved margs (Phase 4). Each becomes a concrete ExtendDef with
        its own body, joins `monomorphized_extensions` for the backend's weak_odr
        emission, and has its body's function instantiations collected exactly as the
        struct-target round above does (#392).
        """
        pending = self.tables.pending_extension_instantiations
        if not pending:
            return
        from sushi_lang.semantics.generics.extensions import monomorphize_extension_method

        self.tables.pending_extension_instantiations = []
        fn_instantiations = set()
        new_defs = []
        for template, target_type, receiver_args, method_type_args in pending:
            extend_def = monomorphize_extension_method(
                template, target_type, receiver_args,
                substitutor=monomorphizer.substitutor,
                method_type_args=method_type_args)
            new_defs.append(extend_def)
            self.monomorphized_extensions.append(extend_def)
        self._intern_late_type_instantiations(monomorphizer, new_defs)
        for extend_def in new_defs:
            fn_instantiations |= monomorphizer.collect_from_extension_body(extend_def)
        if fn_instantiations:
            monomorphizer.monomorphize_all_functions(fn_instantiations, compilation_order)

    def _monomorphize_generic_perk_impls(self, monomorphizer, compilation_order,
                                         struct_instantiations, concrete_structs,
                                         enum_instantiations, concrete_enums,
                                         fn_instantiations) -> None:
        """Instantiate every generic-target perk implementation the program names.

        The copy goes home to the unit that DECLARED the template, which is the same
        rule a monomorphized generic function follows (#495): its methods are emitted
        with that unit's symbol prefix, and a second unit's copy of one name would be a
        second definition of one symbol.
        """
        from sushi_lang.semantics.generics.extensions import monomorphize_all_perk_impls

        templates = self.tables.generic_perk_impls
        if not templates:
            return

        copies = monomorphize_all_perk_impls(
            templates, struct_instantiations, concrete_structs,
            enum_instantiations, concrete_enums,
            substitutor=monomorphizer.substitutor)
        if not copies:
            return

        units_by_name = {u.name: u for u in compilation_order if u.ast is not None}
        entry = next((u for u in compilation_order
                      if u.ast is not None and getattr(u, "is_entry", False)), None)
        if entry is None:
            entry = next((u for u in compilation_order if u.ast is not None), None)

        for (type_name, _perk_name), (template, impl) in copies.items():
            if not self.tables.perk_impls.register(impl, type_name,
                                            unit_name=template.unit_name):
                continue
            home = units_by_name.get(template.unit_name) or entry
            if home is not None:
                home.ast.perk_impls.append(impl)
            for method in impl.methods:
                fn_instantiations |= monomorphizer.collect_from_perk_method_body(
                    impl.target_type, method)

    def _cut_templates_for_late_instantiations(self, monomorphizer, compilation_order,
                                               concrete_extension_defs,
                                               struct_instantiations,
                                               enum_instantiations) -> None:
        """Cut the template copies for every instantiation interned AFTER the collector ran.

        The generic-target extension and perk-implementation copies are cut from the
        instantiations the instantiate pass collected. A type a generic BODY names --
        `let Box@(T) b` in `outer@(T)`, or the return of a generic it calls -- is
        interned only while that body is substituted, so `Box<string>` existed and had
        no `show()` (#555). The tables are the authority on what exists: every interned
        instantiation with no copy yet gets one here. A copy's body can instantiate more
        functions and those can intern more types, so this runs to a fixpoint; the bound
        exists so a pathological program cannot spin, and reaching it drops an
        instantiation, which surfaces as the ordinary CE2008, never as a hang.
        """
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        from sushi_lang.semantics.generics.extensions import monomorphize_all_extension_methods

        cut = {instantiation_key(base, args) for base, args in struct_instantiations}
        cut |= {instantiation_key(base, args) for base, args in enum_instantiations}

        def late(table) -> dict:
            return {name: ty for name, ty in table.by_name.items()
                    if getattr(ty, "generic_base", None) and getattr(ty, "generic_args", None)
                    and name not in cut}

        for _round in range(self.MAX_ARRAY_EXPANSION_ROUNDS):
            late_structs = late(self.tables.structs)
            late_enums = late(self.tables.enums)
            if not late_structs and not late_enums:
                return
            cut.update(late_structs)
            cut.update(late_enums)
            struct_insts = {(ty.generic_base, ty.generic_args) for ty in late_structs.values()}
            enum_insts = {(ty.generic_base, ty.generic_args) for ty in late_enums.values()}

            fn_instantiations: set = set()
            self._monomorphize_generic_perk_impls(
                monomorphizer, compilation_order, struct_insts, late_structs,
                enum_insts, late_enums, fn_instantiations)
            copies = monomorphize_all_extension_methods(
                self.tables.generic_extensions.by_type, struct_insts, late_structs,
                enum_insts, late_enums, substitutor=monomorphizer.substitutor)
            for key, extend_def in copies.items():
                if key in concrete_extension_defs:
                    continue
                concrete_extension_defs[key] = extend_def
                fn_instantiations |= monomorphizer.collect_from_extension_body(extend_def)
            if fn_instantiations:
                monomorphizer.monomorphize_all_functions(fn_instantiations, compilation_order)

    def _intern_late_type_instantiations(self, monomorphizer, extend_defs) -> None:
        """Intern the type instantiations a call-site-solved copy names (risk 1).

        A solved method argument can name an instantiation (`List@(bool)`) that appears
        NOWHERE else in the program, and the copy is created after `resolve`,
        `finite-types` and `derive` have run. Every fully-concrete GenericTypeRef in
        the copies' signatures and `let` annotations goes through the late interner.
        """
        from sushi_lang.semantics.generics.monomorphize.functions import let_annotations

        types: list = []
        for extend_def in extend_defs:
            types.append(extend_def.ret)
            types.append(getattr(extend_def, "err_type", None))
            for param in extend_def.params:
                types.append(param.ty)
            types.extend(let_annotations(extend_def.body))

        self._intern_generic_type_refs(monomorphizer, types)

    def _intern_generic_type_refs(self, monomorphizer, types) -> None:
        """Monomorphize, resolve, size-check and derive every NEW instantiation `types` name.

        The one late-interning seam: the typecheck pass reaches it through
        `tables.intern_generic_ref` when a call site solves a method-level type
        argument, and the drain reaches it for the copies' bodies.

        Every run here names the instances that are NEW to it. The passes walked both
        tables whole once already, and this seam sits inside a fixpoint loop, so a
        whole-table re-run cost O(all types) for each instance and changed nothing but
        the new names (#676). `names_since` is the one answer to what a table gained.

        ONE window serves resolve, derive and the finite-types walk: this round's own
        instances. A `Result` or a `Maybe` the typecheck pass interns between two rounds
        derives its hash AND its clone at the intern itself (#720), so no later walk has
        to repair it, and an older cycle stopped the analysis already (#677).
        """
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        from sushi_lang.semantics.generics.types import GenericTypeRef

        struct_insts: set = set()
        enum_insts: set = set()

        def resolve_ref(ty):
            """A concrete Type for `ty`, queueing what is not interned yet."""
            if not isinstance(ty, GenericTypeRef):
                return ty
            args = tuple(resolve_ref(a) for a in ty.type_args)
            if any(a is None or isinstance(a, GenericTypeRef) for a in args):
                return None
            key = instantiation_key(ty.base_name, args)
            interned = self.tables.structs.by_name.get(key) or self.tables.enums.by_name.get(key)
            if interned is not None:
                return interned
            if ty.base_name in self.tables.generic_structs.by_name:
                struct_insts.add((ty.base_name, args))
            elif ty.base_name in self.tables.generic_enums.by_name:
                enum_insts.add((ty.base_name, args))
            return None

        for ty in types:
            if ty is not None:
                resolve_ref(ty)

        if not struct_insts and not enum_insts:
            return

        from sushi_lang.semantics.passes.finite_types import (
            check_infinite_size_types, names_since, table_marks)
        marks = table_marks(self.tables.structs, self.tables.enums)
        monomorphizer.monomorphize_all(self.tables.generic_enums.by_name, enum_insts)
        monomorphizer.monomorphize_all_structs(self.tables.generic_structs.by_name, struct_insts)

        # One list for both tables: a type name is one per program, so each table takes
        # the names it holds.
        new_structs, new_enums = names_since(self.tables.structs, self.tables.enums, marks)
        interned = [*new_structs, *new_enums]

        from sushi_lang.semantics.passes.resolve import (
            resolve_enum_variant_types, resolve_struct_field_types)
        resolve_struct_field_types(self.tables.structs, self.tables.enums, only=interned)
        resolve_enum_variant_types(self.tables.structs, self.tables.enums, only=interned)

        # A late-solved instance can hold itself by value, and the whole-program run of
        # finite-types is over: walk it again from the new names alone (#677).
        check_infinite_size_types(self.tables.structs, self.tables.enums, self.reporter, since=marks)

        from sushi_lang.semantics.passes.derive import (
            register_all_array_hashes, register_all_clones,
            register_all_enum_hashes, register_all_struct_hashes)
        register_all_struct_hashes(self.tables.structs, self.tables.derived_methods,
                                   only=interned)
        register_all_enum_hashes(self.tables.enums, self.tables.derived_methods,
                                 only=interned)
        register_all_array_hashes(self.tables.structs, self.tables.enums,
                                  self.tables.derived_methods, only=interned)
        register_all_clones(self.tables.structs, self.tables.enums,
                            self.tables.derived_methods, only=interned)

    def _check_monomorphized_extensions(self, destroy_effects, enum_names,
                                        lift_target=None, only=None) -> None:
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
        capture_scope = ScopeAnalyzer(throwaway, self.tables.constants, self.tables.structs, self.tables.enums,
                                      self.tables.generic_enums, self.tables.generic_structs,
                                      external_table=self.tables.externals)
        capture_scope.function_names = set(self.tables.funcs.by_name)

        lifter = None
        if lift_target is not None:
            from sushi_lang.semantics.passes.lift import LambdaLifter
            lifter = LambdaLifter(self.tables.structs, self.tables.funcs, lift_target,
                                  annotate=type_validator)

        for extend_def in (only if only is not None else self.monomorphized_extensions):
            capture_scope._check_extension_method(extend_def)
            type_validator._validate_extension_method(extend_def)
            lifted = lifter.lift_body(extend_def.body) if lifter is not None else []
            for fn in lifted:
                fn.is_public = True
            borrow_checker._check_extension(extend_def)
            for fn in lifted:
                borrow_checker._check_function(fn)

        seen = {diagnostic_identity(d) for d in self.reporter.items}
        for diagnostic in scratch.items:
            identity = diagnostic_identity(diagnostic)
            if identity in seen:
                continue
            seen.add(identity)
            self.reporter.items.append(diagnostic)

    def _check_extension_shadows_builtin(self) -> None:
        """Reject an extension method that collides with a built-in (CE2097)."""
        from sushi_lang.internals import errors as er
        from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
        from sushi_lang.semantics.generics.type_display import display_type

        for target_type, methods in self.tables.extensions.by_type.items():
            for method_name, method in methods.items():
                if not builtin_method_exists(target_type, method_name,
                                             self.tables.derived_methods):
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

    def _check_entrypoint(self, compilation_order: list[Unit]) -> None:
        """Main's rule, whole. The ONE home of it (#674).

        It used to have three: the pipeline asked whether a main exists (CE3007) and
        whether a library carries one (CE3501), the collect pass read its return type
        (CE0106), and this pass -- named for main's signature -- checked no signature at
        all and set one bool.

        The order is the ruling's: the entry point exists, a library refuses one, the
        return is an integer, and the parameter is `string[] args` or nothing. The last
        answers `main_expects_args`, which the back end reads to hand argv on.
        """
        from sushi_lang.internals import errors as er
        from sushi_lang.semantics.generics.type_display import display_type
        from sushi_lang.semantics.type_predicates import is_integer_type
        from sushi_lang.semantics.typesys import DynamicArrayType

        mains = [func for unit in compilation_order if unit.ast is not None
                 for func in unit.ast.functions if func.name == "main"]

        if self.is_library:
            # A library must not carry main(): `--lib` used to embed it into the .slib
            # silently, where it collides at link time in every consumer.
            for func in mains:
                er.emit(self.reporter, er.ERR.CE3501, func.name_span)
        elif not mains:
            # Without this the missing `_main` symbol reached the LINKER, so the user
            # got raw `cc` stderr and then a CE0000 "this is a compiler bug" -- for a
            # condition in their own program (#251).
            er.emit_with(self.reporter, er.ERR.CE3007, None) \
                .help("add `fn main() i32:` to the program, or compile it as a library "
                      "with `--lib`").emit()

        for func in mains:
            ret_ty = func.ret
            if ret_ty is not None and not is_integer_type(ret_ty):
                er.emit(self.reporter, er.ERR.CE0106,
                        getattr(func, "ret_span", None) or func.name_span,
                        type=display_type(ret_ty))

        self.main_expects_args = any(
            param.name == "args"
            and isinstance(param.ty, DynamicArrayType)
            and param.ty.base_type == BuiltinType.STRING
            for param in (mains[0].params if mains else ()))
