from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import (
    Reporter, diagnostic_identity, in_source_order)
from sushi_lang.semantics.ast import ExtendDef, ExtendWithDef, FuncDef
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.passes.collect.dont_panic import MarkerGate
from sushi_lang.semantics.tables import SymbolTables

if TYPE_CHECKING:
    from sushi_lang.semantics.library_registry import LibraryRegistry
    from sushi_lang.semantics.generics.instantiate import InstantiationCollector
    from sushi_lang.semantics.generics.monomorphize import Monomorphizer
    from sushi_lang.semantics.generics.array_perk_copies import ArrayPerkCopies
from sushi_lang.semantics.passes.scope import ScopeAnalyzer
from sushi_lang.semantics.passes.types import (
    TypeValidator, validate_perk_implementation_method)
from sushi_lang.semantics.passes.borrow import BorrowChecker
from sushi_lang.semantics.passes.lift import LambdaLifter
from sushi_lang.semantics.units import UnitManager, Unit
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.generics.extensions import monomorphize_all_extension_methods
from sushi_lang.semantics.generics.monomorphize.order import diagnostics_in_site_order
from sushi_lang.semantics.library_registration import (
    LibraryRegistration, LoadedLibraries)


# What `monomorphize_all_extension_methods` answers: one concrete ExtendDef per
# (target type, method name, type arguments).
# A generic-target extension copy, by (target, method, type arguments, declaring unit).
ExtensionCopies = dict[tuple[str, str, tuple, Optional[str]], ExtendDef]


def enum_base_names(*tables) -> set[str]:
    """The base name of every enum in `tables`, for the borrow checker's sink test."""
    names: set[str] = set()
    for table in tables:
        mapping = table.by_name if hasattr(table, 'by_name') else table
        names.update(name.split('<', 1)[0] for name in mapping)
    return names



# The test runner's stdlib gates, hidden like SUSHI_SPELLING_GATE. Set, a lint also checks
# the BUNDLED stdlib units; a source library stays skipped. One for the doc blocks (#953),
# one for the dead-code lint (#959).
STDLIB_DOC_GATE_ENV = "SUSHI_STDLIB_DOC_GATE"
STDLIB_DEAD_GATE_ENV = "SUSHI_STDLIB_DEAD_GATE"


def _lint_checks(unit: Unit, gate_env: Optional[str] = None) -> bool:
    """Does a lint check this unit? Always a unit of the program; a bundled stdlib unit
    only when its stdlib gate is set; a library unit never.

    The one answer to "whose warning is this" (#1007): a unit's reporter keeps its
    warnings only when this says yes, so a consumer hears no lint in code it did not
    write, and the exit status does not change for one."""
    if unit.provenance is None:
        return True
    if gate_env is None or os.environ.get(gate_env, "").lower() in ("", "0", "off"):
        return False
    return unit.is_bundled_stdlib


def _checks_templates(unit: Unit) -> bool:
    """Does the typecheck pass check the function templates of this unit (#1070)?

    A unit of the program -- a library's own units at its `--lib` build included -- and
    a bundled stdlib unit. A consumed library unit is not checked again: its own build
    checked it."""
    return unit.is_authors_unit


@dataclass(frozen=True)
class Lints:
    """The warning-control flags: `--warn-missing-docs` and `--warn-unused`.

    The second warning-control flag is what earned an object (documentation.md R35).
    """
    missing_docs: bool = False
    unused: bool = False


@dataclass(frozen=True)
class _UnitPasses:
    """The four per-unit passes of one unit, built in that unit's scope."""
    scope: ScopeAnalyzer
    typecheck: TypeValidator
    lifter: LambdaLifter
    borrow: BorrowChecker


@dataclass(frozen=True)
class _CopyEntry:
    """The four per-unit pass entries for one kind of copy.

    `borrow` takes the copy and the functions that the lift pass made from its lambdas.
    """
    scope: Callable[[ScopeAnalyzer, Any], None]
    typecheck: Callable[[TypeValidator, Any], None]
    lift: Callable[[LambdaLifter, Any], list]
    borrow: Callable[[BorrowChecker, Any, list], None]


def _written_declaration(method: Any, template_id: Any) -> tuple:
    """The written declaration that an extension record or a template comes from.

    A copy of a template keeps the template's identity and spans, so the copies of one
    declaration, and the template itself, share this key. Two concrete-target
    declarations of one method share a template identity and differ in their spans. A
    written record with no template is its own key.
    """
    if template_id is None:
        return ("written", id(method))
    span = method.name_span or method.loc
    where = None if span is None else (span.line, span.col, span.end_line, span.end_col)
    return ("copy", template_id, where)


def _borrow_lifted(borrow: BorrowChecker, functions) -> None:
    """Borrow-check functions, each as the body that the reporter reads."""
    for fn in functions:
        borrow.reporter.enter_body(fn)
        borrow._check_function(fn)


def _borrow_function_copy(borrow: BorrowChecker, func: Any, lifted: list) -> None:
    _borrow_lifted(borrow, (func, *lifted))


def _borrow_extension_copy(borrow: BorrowChecker, ext: ExtendDef, lifted: list) -> None:
    borrow._check_extension(ext)
    _borrow_lifted(borrow, lifted)


def _borrow_perk_copy(borrow: BorrowChecker, impl: ExtendWithDef, lifted: list) -> None:
    borrow._check_perk_impl(impl)
    _borrow_lifted(borrow, lifted)


# One row per kind of copy that is cut after the per-unit loop: a late function copy, a
# generic-target or array-target extension copy, and an array-template perk copy.
_COPY_ENTRIES: dict[str, _CopyEntry] = {
    "function": _CopyEntry(
        scope=ScopeAnalyzer._check_function,
        typecheck=TypeValidator._validate_function,
        lift=LambdaLifter.lift_body,
        borrow=_borrow_function_copy),
    "extension": _CopyEntry(
        scope=ScopeAnalyzer._check_extension_method,
        typecheck=TypeValidator._validate_extension_method,
        lift=LambdaLifter.lift_body,
        borrow=_borrow_extension_copy),
    "perk": _CopyEntry(
        scope=ScopeAnalyzer._check_perk_implementation,
        typecheck=validate_perk_implementation_method,
        lift=LambdaLifter.lift_perk_impl,
        borrow=_borrow_perk_copy),
}


def _copy_entry(node: Any) -> _CopyEntry:
    """The row of a copy, by its kind. A function copy is a FuncDef or a cut of a
    template record; any other node is no copy, and that is a fault in the compiler."""
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    if isinstance(node, ExtendWithDef):
        return _COPY_ENTRIES["perk"]
    if isinstance(node, ExtendDef):
        return _COPY_ENTRIES["extension"]
    if isinstance(node, (FuncDef, GenericFuncDef)):
        return _COPY_ENTRIES["function"]
    er.raise_internal_error(
        "CE0015", message=f"a copy of no known kind reached the copy check: "
                          f"{type(node).__name__}")


class SemanticAnalyzer:
    """Semantic analysis coordinator that runs all semantic analysis passes."""

    def __init__(self, reporter: Reporter, filename: str = "<input>", unit_manager: Optional[UnitManager] = None, library_linker: Optional[LoadedLibraries] = None, library_registry: Optional['LibraryRegistry'] = None, lints: Optional[Lints] = None,
                 generated_symbols: frozenset[str] = frozenset(),
                 is_library: bool = False, dont_panic: bool = False) -> None:
        self.reporter = reporter
        self.filename = filename
        self.unit_manager = unit_manager
        # A backend LibraryResolver, held through a Protocol: semantics must not
        # import backend (Tier 4.1 layering invariant).
        self.library_linker = library_linker
        self.library_registry = library_registry
        self._refused_pack_bodies: list[str] = []
        # What the stdlib generators define, read from the manifest their build writes.
        # A NAME list and nothing else: no semantic table holds these symbols, which is
        # why CE5013 could not see them (#472).
        self.generated_symbols = generated_symbols
        self.lints = lints if lints is not None else Lints()
        # `--lib`. The `entrypoint` pass is the one home of main's rule and the rule
        # turns on the build kind: an executable needs a main, a library refuses one.
        self.is_library = is_library
        # `--dont-panic`: a unit of this build may mark a function `dont_panic` (D8), and
        # a source library's marker has the consumer's consent (D9). `marked` names each
        # marker outside the bundled stdlib, so the pipeline can tell a flag that marks
        # nothing (D13).
        self.dont_panic = dont_panic
        self.marked: list[str] = []
        # The whole program's symbol tables, and the ONE home of each of them: the
        # `namespaces` a unit may write behind a dot are `tables.namespaces`, and so on
        # for the other eighteen. Empty until the collect loop replaces them with what
        # the collect pass filled -- never None, so no reader needs a guard (#673).
        self.tables: SymbolTables = SymbolTables()
        self.monomorphized_extensions: list['ExtendDef'] = []  # Concrete ExtendDef nodes for codegen
        self.library_perk_impls: list['ExtendWithDef'] = []  # Library-shipped impls registered here (declare-only at codegen)
        # The array-template perk copies cut on demand (#699); set with the monomorphizer.
        self.array_perk_copies: Optional['ArrayPerkCopies'] = None
        self.library_extensions: list[ExtendDef] = []  # Library-shipped extension methods, declare-only too
        self.main_expects_args: bool = False  # Whether main function has string[] args parameter

    def check(self) -> None:
        """Entry point for semantic analysis. Runs every pass in sequence.

        This docstring is the authority on the pass order. The passes have NAMES, not
        numbers -- a number goes out of order the moment a pass is inserted, which is how
        the old scheme ended up running the scope pass after the derive pass.

        `_check_multi_file` IS this order in code: one call per stage, named for it.

            stage         what it does                           method                            where it lives
            collect       constants, headers, generic types      _collect                          passes/collect/
            docs          doc blocks against their declarations  _check_docs                       passes/docs.py
            unused        --warn-unused: dead privates, imports  _check_unused                     unused.py
            externs       extern signatures, ptr unit gate       _check_externs                    passes/types/externals.py
            libraries     library symbol registration            _register_libraries               library_registration.py
            namespaces    `use ... as`, one table per unit       _build_namespaces                 passes/namespaces.py
            ffi-clash     link-name constants, then an extern    _check_ffi_clash                  passes/types/externals.py
                          naming a defined symbol, then two                                        passes/collect/externals.py
                          declarations of one C symbol with
                          other C types (CE5001)
            entrypoint    main(): it exists, returns i32         _check_entrypoint                 here
            instantiate   generic instantiation collection       _collect_instantiations           generics/instantiate/
            monomorphize  generic -> concrete                    _monomorphize                     generics/monomorphize/
            resolve       field, variant and Result return types _resolve_types                    passes/resolve.py
            finite-types  reject by-value containment cycles     _check_finite_types               passes/finite_types.py
            derive        auto-derived hash() and clone()        _derive                           passes/derive.py
            shadowing     reject an extension over a built-in    _check_extension_shadows_builtin  here
                          or a stdlib method on a built-in
                          type (C2), then warn where an own      _warn_hidden_imported_extensions  here
                          extension hides an imported one (C3)
            effects       destroy-effect summary                 _compute_effects                  passes/borrow/destroy_effects.py
            scope         scope and variable analysis            _check_units                      passes/scope.py
            typecheck     type validation and inference          _check_units                      passes/types/
            lift          lambda lifting                         _check_units                      passes/lift.py
            borrow        borrow checking                        _check_units                      passes/borrow/

        The last four run per unit, in one loop. `_check_copies` repeats those four for
        each copy cut after the loop started: an extension copy of a generic or an array
        target, an array-template perk copy, and a function instance of a late request
        of the typecheck pass (#1155). `_check_array_extensions` drives it to a
        fixpoint. A copy of a checked template reports only the per-instance remainder.

        The `typecheck` pass also checks each template of the unit one time, on a check
        copy, where it is written (#1070, `passes/types/templates.py`): each type
        parameter is opaque, the copy lives in an overlay of the tables, and the pass runs
        `lift` and, through a hook, `borrow` on the copy alone. After the fixpoint, `_reject_opaque_in_program_tables`
        is the backstop (CE0148): no instance over an opaque parameter reached a program
        table.

        One call in `_check_multi_file` carries no row, because it is not a pass:
        `_register_monomorphized_extensions` merges the generic-target extension copies
        into the extension table. It is the `monomorphize` stage's tail and both ends of
        its placement -- after `derive`, before `shadowing` -- are load-bearing.

        `ffi-clash` has a second half with no row of its own: `_check_instance_clash`
        runs CE5013 over the monomorphized function instances, directly after
        `monomorphize`, because no instance exists before it (#1113).

        `semantics/const_eval.py` is NOT a pass. THREE callers reach it as a helper: the
        AST BUILDER, which reads a fixed array's size while the unit is parsed (Known
        Limitation 12) and keeps a constant table of its own for it; the `typecheck`
        pass; and the backend. The last two share the collect pass's table, and with it
        the fold memo (#597).
        """
        try:
            self._check_multi_file()
        finally:
            # A miss in the backend is a miss: every copy it needs was cut here.
            self.tables.perk_impls.on_array_miss = None

    def _unit_reporter(self, unit, gate_env: Optional[str] = None) -> Reporter:
        """A Reporter that knows one unit's file and source, for a per-unit pass.

        `gate_env` names the stdlib gate of a lint pass, which keeps a stdlib unit's
        warnings while it is set. The reporter reads the refused and the checked
        templates of the analysis: a copy of a refused template reports nothing, and a
        copy of a checked one reports only the per-instance remainder (#1070)."""
        reporter = Reporter(source=unit.read_source(), filename=str(unit.file_path),
                            provenance=unit.provenance,
                            keeps_warnings=_lint_checks(unit, gate_env))
        reporter.refused_templates = self.tables.refused_templates
        reporter.checked_templates = self.tables.checked_templates
        reporter.template_warnings = self.tables.template_warnings
        return reporter

    def _merge_unit(self, unit_reporter: Reporter) -> None:
        """Hand one unit's findings to the program reporter, in source order.

        The one place a per-unit reporter is drained. The passes emit in pass order and
        a unit is walked whole by each of them, so a fault the `lift` pass found sits
        behind every fault the `typecheck` pass found -- source order is what a reader
        asked for (#629).
        """
        self.reporter.items.extend(in_source_order(unit_reporter.items))

    def _check_multi_file(self) -> None:
        """Multi-file semantic analysis with cross-unit symbol resolution.

        One call per named stage, in the order `check()`'s docstring states. A stage
        that stops the analysis says so with its return value.
        """
        if self.unit_manager is None:
            return

        compilation_order = self.unit_manager.get_compilation_order()
        if compilation_order is None:
            return  # Error already reported

        # A stage that emits through the program reporter at a site in a library unit
        # gives the note a per-unit reporter gives.
        for unit in compilation_order:
            if unit.provenance is not None:
                self.reporter.add_provenance(str(unit.file_path), unit.provenance)

        libraries = self._collect(compilation_order)
        self._check_docs(compilation_order)
        self._check_unused(compilation_order, self.unit_manager.units)
        self._check_externs(compilation_order)
        self._register_libraries(compilation_order, libraries)
        self._build_namespaces(compilation_order, self.unit_manager.units)
        self._check_ffi_clash(compilation_order)
        self._check_entrypoint(compilation_order)

        # A type pack named as one type (CE0147), a misplaced `expand` or a type pack on a
        # struct, an enum or a method (CE0115) has no copy the monomorphize pass can cut, and each
        # use would only read the fault back, so the analysis stops before the generic
        # passes.
        if self._refused_pack_bodies:
            return

        instantiations = self._collect_instantiations(compilation_order, libraries)
        monomorphizer, concrete_extension_defs = self._monomorphize(
            compilation_order, instantiations)
        self._check_instance_clash(compilation_order)

        # A constraint violation STOPS the whole-program analysis here (#579, Ruling 4),
        # as CE2095 does below. CE4006 stands at the type that named the refused
        # instantiation, no copy was cut for it, and the per-unit passes would only
        # read the same fault back as a CE2008 from inside a template body. A library
        # type a consumer declaration took from a library stops it for the same reason:
        # CE3011 stands at the declaration, and one name means two shapes to the two
        # sides, so a later pass measures one side's code against the other's (#761,
        # #814, #902 -- binary and source alike).
        if monomorphizer.constraint_violations or libraries.refused_types:
            return

        self._resolve_types()
        if self._check_finite_types():
            return
        self._derive()
        self._register_monomorphized_extensions(concrete_extension_defs, compilation_order)
        self._check_extension_shadows_builtin(monomorphizer.sites)
        self._warn_hidden_imported_extensions()

        destroy_effects = self._compute_effects(compilation_order)
        # Enum type names for the borrow pass's ownership-sink test, stripped to their
        # base name: a monomorphized generic enum is interned as "Result<i32, StdError>"
        # while its constructor is written `Result.Ok(...)`.
        enum_names = enum_base_names(self.tables.enums, self.tables.generic_enums)

        self._start_late_requests(monomorphizer, compilation_order)
        self._check_units(compilation_order, monomorphizer, libraries,
                          destroy_effects, enum_names)
        self._check_array_extensions(compilation_order, monomorphizer, libraries,
                                     destroy_effects, enum_names)
        self._reject_opaque_in_program_tables()

    def _reject_opaque_in_program_tables(self) -> None:
        """CE0148, the backstop of the template check (#1070): no opaque type reached here.

        The check builds its instances in an overlay; a writer that missed the overlay
        would hand the backend a type parameter. One scan, one time: each name, and each
        type a field or a payload holds -- a lambda environment of a check copy has a
        plain name and an opaque field. The walk stops at every other declaration, so
        each entry is read once.
        """
        from sushi_lang.internals.errors import raise_internal_error
        from sushi_lang.semantics.generics.types import OPAQUE_MARK, TypeParameter
        from sushi_lang.semantics.type_walk import DECLARATION_KINDS, walk_named_types
        # What the tables hold: a name the order keeps and the table dropped is no type.
        entries = [entry for table in (self.tables.structs, self.tables.enums)
                   for entry in map(table.by_name.get, table.order) if entry is not None]
        def other_declaration(top):
            return lambda ty: ty is not top and type(ty).__name__ in DECLARATION_KINDS

        for entry in entries:
            if OPAQUE_MARK in entry.name:
                raise_internal_error("CE0148", name=entry.name)
            for held in walk_named_types(entry, stop=other_declaration(entry)):
                if isinstance(held, TypeParameter) and held.is_opaque:
                    raise_internal_error("CE0148", name=entry.name)

    def _collect(self, compilation_order: list[Unit]) -> LibraryRegistration:
        """collect: constants, headers and generic types, from every unit.

        It answers the `libraries` step's registration, which is built here because the
        perk definitions it seeds have to reach the tables before the first unit.
        """
        collector = CollectorPass(
            self.reporter,
            library_units={u.name for u in compilation_order if u.provenance is not None},
        )
        # The collect pass fills ONE set of tables, in place, once per unit -- so what
        # it holds IS the whole program's answer and nothing copies it into a second
        # set (#672).
        global_tables = collector.tables

        libraries = LibraryRegistration(self.reporter, global_tables,
                                        self.library_linker, self.library_registry,
                                        dont_panic=self.dont_panic)
        # BEFORE the consumer's units: perk-impl collection validates each impl against
        # the visible perk definitions (CE4003), so the contract must already be here.
        # A library's generic TYPES are seeded for the same reason one kind on: a
        # consumer's `extend Crate@(T)` names one in its TARGET, and the collect pass
        # reads that name while it collects the unit (#728).
        if self.library_linker is not None:
            libraries.seed_perks(global_tables.perks)
            libraries.seed_generic_types()
            collector.admit_binary_libraries(libraries.library_names())

        for unit in compilation_order:
            if unit.ast is None:
                continue

            collector.run(unit.ast, unit_name=unit.name,
                          unit_file=str(unit.file_path),
                          marker_gate=MarkerGate.for_unit(unit, self.dont_panic))

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

        libraries.refused_types.extend(collector.refused_library_types)
        self._refused_pack_bodies = collector.refused_pack_bodies
        self.marked.extend(collector.marked)
        self.tables = global_tables
        return libraries

    def _check_docs(self, compilation_order: list[Unit]) -> None:
        """docs: check each doc block against the declaration beside it."""
        # Here because the pass needs the collected tables and nothing later, and because
        # it must run ahead of instantiate/monomorphize -- a generic's block is written
        # once, and checking it afterwards would report one mistake once per
        # instantiation. A library unit is skipped: a consumer must not be told about the
        # library author's doc typos.
        from sushi_lang.semantics.passes.docs import check_docs, check_missing_docs
        for unit in compilation_order:
            if unit.ast is None:
                continue
            if not _lint_checks(unit, STDLIB_DOC_GATE_ENV):
                continue
            unit_reporter = self._unit_reporter(unit, STDLIB_DOC_GATE_ENV)
            check_docs(unit_reporter, unit.ast)
            # Completeness rides in the SAME loop, and nowhere later:
            # `register_synthesized_function` appends a monomorphized clone to a unit's
            # own `ast.functions`, so a lint that ran after `monomorphize` would demand
            # a doc block on every instance the program asked for.
            if self.lints.missing_docs:
                check_missing_docs(unit_reporter, unit.ast)
            self._merge_unit(unit_reporter)

    def _check_unused(self, compilation_order: list[Unit], all_units: dict) -> None:
        """unused: `--warn-unused`, a dead private declaration and an unused import."""
        # Beside `docs` and for its reason: it reads the WRITTEN declarations, before
        # `libraries` appends a binary library's constants to a host unit and before
        # `monomorphize` appends the instances.
        if not self.lints.unused:
            return
        from sushi_lang.semantics.unused import check_unused
        for unit in compilation_order:
            if unit.ast is None or not _lint_checks(unit, STDLIB_DEAD_GATE_ENV):
                continue
            unit_reporter = self._unit_reporter(unit, STDLIB_DEAD_GATE_ENV)
            check_unused(unit_reporter, unit, self.tables, all_units)
            self._merge_unit(unit_reporter)

    def _check_externs(self, compilation_order: list[Unit]) -> None:
        """externs: extern signatures (CE5003), CW5001, and the ptr unit gate (CE5009)."""
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

    def _register_libraries(self, compilation_order: list[Unit],
                            libraries: LibraryRegistration) -> None:
        """libraries: every symbol a binary `.slib` exports enters the collected tables."""
        # The order of the arms is the step's own (`library_registration.py`); the perk
        # DEFINITIONS were seeded ahead of the collect loop.
        libraries.register(compilation_order)
        self.library_registry = libraries.registry
        self.library_perk_impls = libraries.shipped_perk_impls
        self.library_extensions = libraries.shipped_extensions
        self.marked.extend(libraries.marked)

    def _build_namespaces(self, compilation_order: list[Unit], all_units: dict) -> None:
        """namespaces: what each unit may write behind a dot."""
        # After `libraries`, because a BINARY library's declarations exist only once that
        # step has read the manifest, and before `ffi-clash`, which is the first step that
        # asks whether a name is already taken (`unit-namespaces.md` section 3.2).
        from sushi_lang.semantics.passes.namespaces import (
            build_compiled_library_namespaces, build_namespaces,
        )
        for unit in compilation_order:
            if unit.ast is None:
                continue
            unit_reporter = self._unit_reporter(unit)
            self.tables.namespaces[unit.name] = build_namespaces(
                unit_reporter, unit, self.tables, units=all_units,
                library_registry=self.library_registry)
            self._merge_unit(unit_reporter)
        # A compiled library's units have no AST here, and a copy of one of their
        # templates still resolves its names in their scope (#1120).
        for unit_name, table in build_compiled_library_namespaces(
                self.tables, units=all_units,
                library_registry=self.library_registry).items():
            self.tables.namespaces.setdefault(unit_name, table)

    def _check_ffi_clash(self, compilation_order: list[Unit]) -> None:
        """ffi-clash: fold the link-name constants (#1089), then an extern naming a symbol
        this build defines (CE5013), then two declarations of one symbol (CE5001)."""
        # An `unsafe external` may name a FOREIGN symbol, never one this build defines
        # (#470). It reads the whole program's symbols, the linked libraries included, so
        # it cannot run with the per-unit extern validation above -- the registry does
        # not exist yet up there.
        from sushi_lang.semantics.passes.types.externals import (
            fold_link_names, reject_external_naming_a_defined_symbol,
        )
        for unit in compilation_order:
            if unit.ast is None:
                continue
            unit_reporter = self._unit_reporter(unit)
            fold_link_names(unit_reporter, unit.ast, self.tables, unit.name)
            reject_external_naming_a_defined_symbol(
                unit_reporter, unit.ast, self.tables, self.library_registry,
                self.generated_symbols)
            self._merge_unit(unit_reporter)

        # One C symbol has one signature in the program (#1099). The names of every unit
        # are folded by now, and the built-ins are the first declaration of their names.
        from sushi_lang.semantics.passes.collect.externals import (
            LinkNames, reject_disagreeing_link_names,
        )
        link_names = LinkNames()
        for unit in compilation_order:
            if unit.ast is None:
                continue
            unit_reporter = self._unit_reporter(unit)
            reject_disagreeing_link_names(unit_reporter, unit.ast, self.tables.externals,
                                          link_names)
            self._merge_unit(unit_reporter)

    def _check_instance_clash(self, compilation_order: list[Unit]) -> None:
        """ffi-clash, the instance half: an extern naming a monomorphized instance (CE5013).

        It runs after `monomorphize`, because no instance exists before it (#1113).
        """
        from sushi_lang.semantics.passes.types.externals import (
            monomorphized_instances, reject_external_naming_an_instance,
        )
        instances = monomorphized_instances(compilation_order, self.tables.funcs)
        if not instances:
            return
        for unit in compilation_order:
            if unit.ast is None or not getattr(unit.ast, "externals", None):
                continue
            unit_reporter = self._unit_reporter(unit)
            reject_external_naming_an_instance(unit_reporter, unit.ast, instances)
            self._merge_unit(unit_reporter)

    def _collect_instantiations(self, compilation_order: list[Unit],
                                libraries: LibraryRegistration) -> 'InstantiationCollector':
        """instantiate: every generic instantiation the program asks for."""
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
        return instantiation_collector

    def _monomorphize(self, compilation_order: list[Unit],
                      instantiations: 'InstantiationCollector',
                      ) -> tuple['Monomorphizer', ExtensionCopies]:
        """monomorphize: every generic the program names becomes a concrete declaration.

        The work order is load-bearing, and it is not the order a user reads the faults
        in: the stage's diagnostics are put in site order once it ends (#927).
        """
        first = len(self.reporter.items)
        copies = self._monomorphize_in_work_order(compilation_order, instantiations)
        self.reporter.items[first:] = diagnostics_in_site_order(
            self.reporter.items[first:], instantiations.sites)
        return copies

    def _monomorphize_in_work_order(self, compilation_order: list[Unit],
                                    instantiations: 'InstantiationCollector',
                                    ) -> tuple['Monomorphizer', ExtensionCopies]:
        type_instantiations = instantiations.instantiations
        func_instantiations = instantiations.function_instantiations

        monomorphizer = self._new_monomorphizer(instantiations)
        monomorphizer.type_monomorphizer.refuse_template_arity(compilation_order)
        enum_instantiations, struct_instantiations = self._resolved_instantiations(
            type_instantiations)

        # Both publish into the tables at creation (`TypeMonomorphizer._publish`), the
        # nested instances included.
        concrete_enums = monomorphizer.monomorphize_all(
            self.tables.generic_enums.by_name, enum_instantiations)
        concrete_structs = monomorphizer.monomorphize_all_structs(
            self.tables.generic_structs.by_name, struct_instantiations)

        self._adopt_reached_instances(monomorphizer,
                                      enum_instantiations, concrete_enums,
                                      struct_instantiations, concrete_structs)

        # A perk implementation on a GENERIC target is instantiated BEFORE the functions
        # are, and that order is load-bearing: a `@(S: Show)` constraint is checked while
        # a generic function is monomorphized, so `Box@(i32)` has to already say it
        # implements `Show` or the call is CE4006 for a type that does.
        perk_impl_fn_instantiations: set = set()
        self._monomorphize_generic_perk_impls(
            monomorphizer, compilation_order, struct_instantiations, concrete_structs,
            enum_instantiations, concrete_enums, perk_impl_fn_instantiations)

        monomorphizer.monomorphize_all_functions(func_instantiations, compilation_order)

        concrete_extension_defs = self._monomorphize_generic_extensions(
            monomorphizer, compilation_order, struct_instantiations, concrete_structs,
            enum_instantiations, concrete_enums, perk_impl_fn_instantiations)

        # monomorphize (cont.): a LATE instantiation -- one the instantiate pass never
        # saw, interned while a generic body was substituted -- gets its generic-target
        # extension and perk-implementation copies exactly as an early one did (#555).
        self._cut_templates_for_late_instantiations(
            monomorphizer, compilation_order, concrete_extension_defs,
            struct_instantiations, enum_instantiations)

        # Every instantiation the program names now exists, so an implementation whose
        # target still names none is one the program never reached.
        self._drop_unreached_perk_impls(compilation_order)
        return monomorphizer, concrete_extension_defs

    def _new_monomorphizer(self, instantiations: 'InstantiationCollector') -> 'Monomorphizer':
        """The monomorphizer, its constraint validator, and the late-intern seam."""
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
            sites=instantiations.sites,
            parents=instantiations.parents,
        )

        # The late-interning seam (risk 1 of the UFCS epic): when the per-unit typecheck
        # solves a method-level type argument, the substituted signature can name an
        # instantiation nothing else names -- reachable from the pass through the tables,
        # because the pass has no monomorphizer of its own.
        self.tables.intern_generic_ref = (
            lambda ty: self._intern_generic_type_refs(monomorphizer, (ty,)))

        self.tables.check_call_constraints = monomorphizer.check_call_constraints

        # The array-template perk copies are cut on demand from here on (#699): the
        # constraint check of the next stage is the first reader that can miss.
        from sushi_lang.semantics.generics.array_perk_copies import ArrayPerkCopies
        self.array_perk_copies = ArrayPerkCopies(self.tables, monomorphizer.substitutor)
        self.tables.perk_impls.on_array_miss = self.array_perk_copies
        return monomorphizer

    def _resolved_instantiations(self, type_instantiations) -> tuple[set, set]:
        """Split the collected type instantiations into enums and structs, arguments resolved."""
        from sushi_lang.semantics.generics.late_interning import resolved_instantiations
        return resolved_instantiations(self.tables, type_instantiations)

    def _adopt_reached_instances(self, monomorphizer, enum_instantiations, concrete_enums,
                                 struct_instantiations, concrete_structs) -> None:
        """Treat an instance a substitution REACHED as an instantiation the program named."""
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

    def _monomorphize_generic_extensions(self, monomorphizer, compilation_order,
                                         struct_instantiations, concrete_structs,
                                         enum_instantiations, concrete_enums,
                                         fn_instantiations) -> ExtensionCopies:
        """Cut every generic-target extension's copy, and monomorphize what its body names."""
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

        extension_fn_instantiations = set(fn_instantiations)
        for extend_def in concrete_extension_defs.values():
            extension_fn_instantiations |= monomorphizer.collect_from_extension_body(extend_def)

        if extension_fn_instantiations:
            monomorphizer.monomorphize_all_functions(extension_fn_instantiations, compilation_order)
        return concrete_extension_defs

    def _resolve_types(self) -> None:
        """resolve: struct field, enum variant, constant and spelled Result return types."""
        # AFTER monomorphization, so every struct/enum exists in the tables.
        from sushi_lang.semantics.passes.resolve import (
            resolve_constant_types, resolve_enum_variant_types, resolve_function_returns,
            resolve_struct_field_types)
        resolve_struct_field_types(self.tables.structs, self.tables.enums)
        resolve_enum_variant_types(self.tables.structs, self.tables.enums)
        resolve_constant_types(self.tables.constants, self.tables.structs, self.tables.enums)
        resolve_function_returns(self.unit_manager.units.values(),
                                 self.tables.structs, self.tables.enums)

    def _check_finite_types(self) -> bool:
        """finite-types: reject a type that contains itself by value (CE2095)."""
        # True stops the analysis -- every later pass assumes finitely-sized types.
        from sushi_lang.semantics.passes.finite_types import check_infinite_size_types
        return check_infinite_size_types(self.tables.structs, self.tables.enums,
                                         self.reporter)

    def _derive(self) -> None:
        """derive: the auto-derived hash() and clone() for every type."""
        # AFTER type resolution, and structs/enums before arrays, which may contain them.
        # The clone half carries no ordering constraint (#134).
        from sushi_lang.semantics.passes.derive import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes,
            register_all_clones,
        )
        register_all_struct_hashes(self.tables.structs, self.tables.derived_methods)

        register_all_enum_hashes(self.tables.enums, self.tables.derived_methods)

        register_all_array_hashes(self.tables.structs, self.tables.enums, self.tables.derived_methods)

        register_all_clones(self.tables.structs, self.tables.enums, self.tables.derived_methods)

    def _register_monomorphized_extensions(
            self, concrete_extension_defs: ExtensionCopies,
            compilation_order: list[Unit]) -> None:
        """Hand every generic-target extension copy to the extension table and to codegen."""
        for extend_def in concrete_extension_defs.values():
            # The unit that DECLARED the template. Adoption may send the copy to another
            # unit of the build, but method resolution reads who declared it.
            declared_in = extend_def.home_unit
            self._adopt_extension_copy(extend_def, compilation_order)
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
                unit_name=declared_in,
                filename=getattr(extend_def, "template_file", None),
                is_public=extend_def.is_public,
                template_id=extend_def.template_id,
                template_target=extend_def.template_target,
            )
            self.tables.extensions.add_method(extension_method)

    def _compute_effects(self, compilation_order: list[Unit]):
        """effects: which functions destroy a `poke` parameter, transitively (#168)."""
        # Computed ONCE across EVERY unit -- the borrow pass runs per unit, so a per-unit
        # summary would make a cross-unit callee invisible.
        from sushi_lang.semantics.passes.borrow import compute_destroy_effects
        return compute_destroy_effects(
            unit.ast for unit in compilation_order if unit.ast is not None
        )

    def _check_units(self, compilation_order: list[Unit], monomorphizer,
                     libraries: LibraryRegistration, destroy_effects,
                     enum_names: set[str]) -> None:
        """scope, typecheck, lift and borrow, per unit, in one loop."""
        # Every unit is analysed with the global context, because units reference each
        # other, but each gets its own reporter so a diagnostic names the right file.
        for unit in compilation_order:
            if unit.ast is None:
                continue

            unit_reporter = self._unit_reporter(unit)
            passes = self._unit_passes(unit, unit_reporter, monomorphizer,
                                       libraries, destroy_effects, enum_names)
            passes.scope.run(unit.ast)
            passes.typecheck.run(unit.ast)
            passes.lifter.run()
            passes.borrow.run(unit.ast, skip=passes.typecheck.checked_template_ids)

            self._merge_unit(unit_reporter)

    def _unit_passes(self, unit: Unit, reporter: Reporter,
                     monomorphizer, libraries: LibraryRegistration, destroy_effects,
                     enum_names: set[str]) -> _UnitPasses:
        """The scope, typecheck, lift and borrow passes of ONE unit, in its own scope.

        Two callers: the per-unit loop, and the check of the generic-target extension
        copies, which each go home to the unit of their template (#1064). A copy is
        checked with the unit name and the namespace table of that unit, so a private
        name and a name behind an alias mean in the copy what they mean in the template.
        """
        namespaces = self.tables.namespaces.get(unit.name)
        scope = ScopeAnalyzer(reporter, self.tables.constants, self.tables.structs,
                              self.tables.enums, self.tables.generic_enums,
                              self.tables.generic_structs, external_table=self.tables.externals,
                              kept_constants=libraries.kept_constant_names(),
                              namespaces=namespaces,
                              visibility=self.tables.visibility,
                              function_tables=(self.tables.funcs, self.tables.generic_funcs),
                              unit_namespaces=self.tables.namespaces)
        # The enum names let the checker tell `Box.Full(a)` from a method call -- both
        # are DotCall here. BASE names only: the receiver is written bare.
        borrow = BorrowChecker(reporter, destroy_effects=destroy_effects,
                               enum_names=enum_names, tables=self.tables,
                               unit_name=unit.name,
                               scope=namespaces.scope if namespaces else None)
        # The typecheck pass calls the borrow pass on each check copy (#1070, R5).
        typecheck = TypeValidator(
            reporter, self.tables, current_unit_name=unit.name,
            monomorphized_functions=monomorphizer.monomorphized_functions,
            in_library_unit=unit.provenance is not None,
            namespaces=namespaces, checks_templates=_checks_templates(unit),
            borrow_check_copy=borrow.check_template_copy)
        lifter = LambdaLifter(self.tables.structs, self.tables.funcs, unit.ast,
                              annotate=typecheck)
        return _UnitPasses(scope, typecheck, lifter, borrow)

    @staticmethod
    def _entry_unit(compilation_order: list[Unit]) -> Optional[Unit]:
        """The unit the compiler was pointed at, and the home of every body it makes.

        A generic-target extension copy or perk implementation whose template names no
        unit of the build goes to this unit (`_home_unit`), for the same reason
        `generics/synthesis.py` names the entry unit: a body the compiler makes belongs to the unit the compiler was pointed at, and not
        to whichever unit the compilation order happens to put first (#736).

        A unit with no AST is never the answer, because every caller reads `.ast` off it.
        """
        entry = next((u for u in compilation_order
                      if u.ast is not None and getattr(u, "is_entry", False)), None)
        if entry is not None:
            return entry
        return next((u for u in compilation_order if u.ast is not None), None)

    def _home_unit(self, unit_name: Optional[str],
                   compilation_order: list[Unit]) -> Optional[Unit]:
        """The unit a template's copy goes home to: the unit that declared the template.

        ONE answer for an extension copy and a perk-implementation copy (#1064). A
        template whose unit is not in the build goes to the entry unit.
        """
        home = next((u for u in compilation_order
                     if u.ast is not None and u.name == unit_name), None)
        return home or self._entry_unit(compilation_order)

    def _library_scope(self, unit_name: Optional[str], home: Optional[Unit]) -> Optional[str]:
        """The compiled library unit a copy's names resolve in, or None (#1120).

        A copy of a compiled library's template goes home to a unit of the consumer,
        because the template's unit is not a unit of this build. Its body still means
        what it meant in the library, so it reads the scope of the declaring unit.
        """
        if unit_name is None or (home is not None and home.name == unit_name):
            return None
        return unit_name if unit_name in self.tables.namespaces else None

    def _adopt_extension_copy(self, extend_def: ExtendDef,
                              compilation_order: list[Unit]) -> None:
        """Give an extension copy its home unit and queue it for the check and codegen."""
        home = self._home_unit(extend_def.home_unit, compilation_order)
        extend_def.scope_unit = self._library_scope(extend_def.home_unit, home)
        extend_def.home_unit = home.name if home is not None else None
        self.monomorphized_extensions.append(extend_def)

    def _check_array_extensions(self, compilation_order: list[Unit], monomorphizer,
                                libraries: LibraryRegistration,
                                destroy_effects, enum_names: set[str]) -> None:
        """Check each call-site-driven extension copy, to a fixpoint."""
        # An array-target template instantiates at the CALL SITE (the typecheck pass
        # queued it), and checking one monomorphized body can resolve a call that queues
        # another. The queue is drained until it is empty: a chain of copies that makes
        # no new type is finite, and its length is not a bound. A chain that grows
        # without end is CE0151 at the copy that the growth rule refuses
        # (`generics/instance_growth.py`), and a refused copy queues nothing more.
        #
        # A function copy cut after the per-unit loop started -- a late request of the
        # typecheck pass, or a call in an extension copy -- is checked in the same round
        # (#1155). Checking it can request another, so it is part of the same fixpoint.
        from sushi_lang.semantics.passes.finite_types import table_marks

        checked = 0
        growing_rounds = 0
        while True:
            marks = table_marks(self.tables.structs, self.tables.enums)
            self._drain_pending_array_extensions(monomorphizer, compilation_order)
            perk_copies = self._place_array_perk_copies(monomorphizer, compilation_order)
            batch = self.monomorphized_extensions[checked:]
            functions = self._place_late_functions(monomorphizer, compilation_order)
            if not batch and not functions and not perk_copies:
                break
            checked = len(self.monomorphized_extensions)
            units = {u.name: u for u in compilation_order if u.ast is not None}
            copies: list[tuple[Unit, Any]] = [
                (units[ext.home_unit or ""], ext) for ext in batch]
            copies += perk_copies
            copies += functions
            self._check_copies(copies, monomorphizer, libraries, destroy_effects,
                               enum_names)
            if marks != table_marks(self.tables.structs, self.tables.enums):
                growing_rounds += 1
            if growing_rounds >= self.MAX_NEW_TYPE_ROUNDS:
                self._refuse_pending_copies(monomorphizer)
                break

    # Rounds of the call-site copy fixpoint that make a new type, before the copies
    # still in the queue are refused. The growth rule refuses a chain that grows
    # without end long before this (`instance_growth.MAX_GROWTH` copies of one
    # template). This bound is for a chain whose parent link the rule cannot read.
    MAX_NEW_TYPE_ROUNDS = 64

    # Rounds `_cut_templates_for_late_instantiations` takes before it stops. The same
    # shape as InstantiationCollector.MAX_EXPANSION_ROUNDS.
    MAX_ARRAY_EXPANSION_ROUNDS = 8

    def _refuse_pending_copies(self, monomorphizer) -> None:
        """CE0151 at the call of each copy still in the queue when the fixpoint stops."""
        pending = self.tables.pending_extension_instantiations
        self.tables.pending_extension_instantiations = []
        for request in pending:
            self._record_copy_request(monomorphizer, request)
            self._refuses_copy(monomorphizer, request, force=True)

    @staticmethod
    def _record_copy_request(monomorphizer, request) -> None:
        """Give the key of a queued copy its site and its parent, the first ones only."""
        monomorphizer.sites.setdefault(request.key, request.site)
        if request.parent is not None:
            monomorphizer.parents.setdefault(request.key, request.parent)

    @staticmethod
    def _refuses_copy(monomorphizer, request, force: bool = False) -> bool:
        """CE0151 when a queued copy makes its chain grow without end (or `force`)."""
        from sushi_lang.semantics.generics.extension_targets import extension_template_id
        from sushi_lang.semantics.generics.type_display import display_type
        template = request.template
        instance = f"{display_type(request.target_type)}.{template.name}"
        if request.method_type_args:
            instance += f"@({', '.join(display_type(a) for a in request.method_type_args)})"
        return monomorphizer.refuses_growth(
            request.key, ("method", extension_template_id(template)),
            (*request.receiver_args, *request.method_type_args), "method", template.name,
            instance, force=force)

    def _drain_pending_array_extensions(self, monomorphizer, compilation_order) -> None:
        """Monomorphize every queued call-site extension instantiation.

        The typecheck pass queued (template, target, receiver args, method args) while
        it resolved calls -- an array template's element (ruling 3) or a
        method-generic's solved margs (Phase 4). Each becomes a concrete ExtendDef with
        its own body, goes home to the unit of its template, and has its body's function instantiations collected exactly as the
        struct-target round above does (#392).
        """
        pending = self.tables.pending_extension_instantiations
        if not pending:
            return
        from sushi_lang.semantics.generics.extensions import monomorphize_extension_method

        self.tables.pending_extension_instantiations = []
        fn_instantiations = set()
        new_defs = []
        for request in pending:
            self._record_copy_request(monomorphizer, request)
            if self._refuses_copy(monomorphizer, request):
                continue
            extend_def = monomorphize_extension_method(
                request.template, request.target_type, request.receiver_args,
                substitutor=monomorphizer.substitutor,
                method_type_args=request.method_type_args)
            extend_def.instance_key = request.key
            new_defs.append(extend_def)
            self._adopt_extension_copy(extend_def, compilation_order)
        self._intern_late_type_instantiations(monomorphizer, new_defs)
        for extend_def in new_defs:
            fn_instantiations |= monomorphizer.collect_from_extension_body(extend_def)
        if fn_instantiations:
            monomorphizer.monomorphize_all_functions(fn_instantiations, compilation_order)

    def _place_array_perk_copies(self, monomorphizer, compilation_order: list[Unit]
                                 ) -> list[tuple[Unit, ExtendWithDef]]:
        """Put each array-template perk copy cut since the last round in its home unit.

        A copy is cut on demand (`generics/array_perk_copies.py`), at any point from the
        `monomorphize` stage on, and its body waits until here: placed during the
        per-unit loop, a copy in a unit the loop has not reached would be checked twice.
        """
        placed: list[tuple[Unit, ExtendWithDef]] = []
        if self.array_perk_copies is None:
            return placed
        fn_instantiations: set = set()
        for template, impl in self.array_perk_copies.take_pending():
            home = self._adopt_perk_copy(template, impl, compilation_order,
                                         monomorphizer, fn_instantiations)
            if home is not None:
                placed.append((home, impl))
        if fn_instantiations:
            monomorphizer.monomorphize_all_functions(fn_instantiations, compilation_order)
        return placed

    def _start_late_requests(self, monomorphizer, compilation_order: list[Unit]) -> None:
        """From the per-unit loop on, a function copy is LATE (#1155).

        The typecheck pass can ask for an instance that the early collection did not
        make, through `tables.request_function_instance`. A copy cut from now on does
        not go into its AST at once, because the per-unit loop is walking the ASTs: it
        waits in `late_bodies`, and `_check_array_extensions` puts it in and checks it.
        """
        monomorphizer.late_bodies = []
        self.tables.request_function_instance = (
            lambda key, site: self._request_function_instance(
                monomorphizer, compilation_order, key, site))

    def _request_function_instance(self, monomorphizer, compilation_order: list[Unit],
                                   key, site) -> bool:
        """Cut the function instance a call asks for. True when a constraint refused it.

        `key` is (declaring unit, name, type arguments) and `site` the (span, file) of
        the call, which is where a constraint refusal points. The copy goes through the
        monomorphizer as an early instance does: its signature is declared now, so the
        call has it, and every instance its body names is cut with it.
        """
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        from sushi_lang.semantics.passes.finite_types import table_marks

        _unit, name, type_args = key
        site_key = ("fn", instantiation_key(name, type_args))
        if site[0] is not None:
            monomorphizer.sites.setdefault(site_key, site)
        marks = table_marks(self.tables.structs, self.tables.enums)
        monomorphizer.monomorphize_all_functions({key}, compilation_order)
        self._settle_new_instances(marks)
        return monomorphizer.was_refused(site_key)

    def _place_late_functions(self, monomorphizer,
                              compilation_order: list[Unit]) -> list[tuple[Unit, FuncDef]]:
        """Put each waiting function copy into its AST, and answer (home unit, copy)."""
        waiting = monomorphizer.late_bodies or []
        monomorphizer.late_bodies = []
        placed = []
        for ast, funcdef in waiting:
            ast.functions.append(funcdef)
            home = next(u for u in compilation_order if u.ast is ast)
            placed.append((home, funcdef))
        return placed

    def _drop_unreached_perk_impls(self, compilation_order) -> None:
        """Forget an implementation whose `@(...)` target names no instance (#698).

        `extend Box@(Point) with Named` is a CONSTRAINT on one instantiation: it holds
        for `Box@(Point)` and for nothing else. Where the program never names that
        instantiation there is no type to check a body against and no method to emit,
        which is the state a generic EXTENSION on the same target is already left in --
        its copy is simply never cut. Leaving the implementation in the walks reached
        the typecheck pass as a CE2001 about the target and the backend as a CE0045.
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        from sushi_lang.semantics.passes.types.perks import validate_unreached_header

        def unreached(impl) -> bool:
            return (isinstance(impl.target_type, GenericTypeRef)
                    and isinstance(
                        resolve_unknown_type(impl.target_type, self.tables.structs.by_name,
                                             self.tables.enums.by_name),
                        GenericTypeRef))

        for unit in compilation_order:
            impls = unit.ast.perk_impls if unit.ast is not None else None
            if not impls:
                continue
            dropped = [impl for impl in impls if unreached(impl)]
            if not dropped:
                continue
            unit.ast.perk_impls = [impl for impl in impls if not unreached(impl)]
            # The header of a dropped one is still a written declaration (#898).
            unit_reporter = self._unit_reporter(unit)
            for impl in dropped:
                validate_unreached_header(self.tables, impl, unit_reporter)
            self._merge_unit(unit_reporter)

    def _monomorphize_generic_perk_impls(self, monomorphizer, compilation_order,
                                         struct_instantiations, concrete_structs,
                                         enum_instantiations, concrete_enums,
                                         fn_instantiations) -> None:
        """Instantiate every generic-target perk implementation the program names.

        The copy goes home to the unit that DECLARED the template, which is the same
        rule a monomorphized generic function follows (#495): its methods are emitted
        with that unit's symbol prefix, and a second unit's copy of one name would be a
        second definition of one symbol.

        A copy that does not register meets a consumer's implementation of a LIBRARY
        template, which is the sanctioned override. A user's template beside a concrete
        implementation of the same perk is refused in the collect pass (CE4002).
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

        for (type_name, _perk_name), (template, impl) in copies.items():
            if not self.tables.perk_impls.register(impl, type_name,
                                            unit_name=template.unit_name):
                continue
            self._adopt_perk_copy(template, impl, compilation_order, monomorphizer,
                                  fn_instantiations)

    def _adopt_perk_copy(self, template, impl, compilation_order: list[Unit],
                         monomorphizer, fn_instantiations: set) -> Optional[Unit]:
        """Put a perk-implementation copy in the home unit of its template.

        The function instances its bodies name are added to `fn_instantiations`.
        Answers the home unit.
        """
        home = self._home_unit(template.unit_name, compilation_order)
        scope_unit = self._library_scope(template.unit_name, home)
        for method in impl.methods:
            method.scope_unit = scope_unit
        if home is not None:
            home.ast.perk_impls.append(impl)
        for method in impl.methods:
            fn_instantiations |= monomorphizer.collect_from_perk_method_body(
                impl.target_type, method, template.filename)
        return home

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
            new_copies = []
            for key, extend_def in copies.items():
                if key in concrete_extension_defs:
                    continue
                concrete_extension_defs[key] = extend_def
                new_copies.append(extend_def)
                fn_instantiations |= monomorphizer.collect_from_extension_body(extend_def)
            self._monomorphize_copy_signatures(monomorphizer, new_copies)
            if fn_instantiations:
                monomorphizer.monomorphize_all_functions(fn_instantiations, compilation_order)

    def _monomorphize_copy_signatures(self, monomorphizer, extend_defs) -> None:
        """Monomorphize every type instantiation a late extension copy names (#1146)."""
        from sushi_lang.semantics.generics.late_interning import intern_copy_signatures
        intern_copy_signatures(self.tables, monomorphizer, extend_defs)

    def _intern_late_type_instantiations(self, monomorphizer, extend_defs) -> None:
        """Intern the type instantiations a call-site-solved copy names (risk 1).

        A solved method argument can name an instantiation (`List@(bool)`) that appears
        NOWHERE else in the program, and the copy is created after `resolve`,
        `finite-types` and `derive` have run. Every fully-concrete GenericTypeRef in
        the copies' signatures and `let` annotations goes through the late interner.
        """
        from sushi_lang.semantics.generics.late_interning import copy_signature_types
        self._intern_generic_type_refs(monomorphizer, copy_signature_types(extend_defs))

    def _intern_generic_type_refs(self, monomorphizer, types) -> None:
        """The one late interner, over the program tables (`generics/late_interning.py`)."""
        from sushi_lang.semantics.generics.late_interning import intern_generic_type_refs
        intern_generic_type_refs(self.tables, monomorphizer, self.reporter, types)

    def _settle_new_instances(self, marks) -> None:
        """Settle what the program tables gained after `marks` (`generics/late_interning.py`)."""
        from sushi_lang.semantics.generics.late_interning import settle_new_instances
        settle_new_instances(self.tables, self.reporter, marks)

    def _check_copies(self, copies: list[tuple[Unit, Any]], monomorphizer,
                      libraries: LibraryRegistration, destroy_effects,
                      enum_names: set[str]) -> None:
        """scope, typecheck, lift and borrow over each copy cut after the per-unit loop.

        A late function copy (#1155), an extension copy of a generic or an array target,
        and an array-template perk copy (#699). Each copy is checked with the passes of
        its HOME unit, the unit that declared the template (#1064): its private functions
        and its aliases are the copy's too. The passes run in the per-unit order, and
        the lifted functions land in the home unit's AST, the one module that defines
        the copy.

        A copy of a checked template reports only the per-instance remainder (#1070, R7):
        the passes run for the stamps that the backend reads. A copy of a consumed
        library's template keeps its full check, and one diagnostic that two copies give
        is merged one time.
        """
        by_home: dict[str, tuple[Unit, list[Any]]] = {}
        for home, node in copies:
            by_home.setdefault(home.name, (home, []))[1].append(node)

        seen = {diagnostic_identity(d) for d in self.reporter.items}
        for home, nodes in by_home.values():
            scratch = self._unit_reporter(home)
            passes = self._unit_passes(home, scratch, monomorphizer, libraries,
                                       destroy_effects, enum_names)
            for node in nodes:
                entry = _copy_entry(node)
                entry.scope(passes.scope, node)
                scratch.leave_body()
                entry.typecheck(passes.typecheck, node)
                scratch.leave_body()
                lifted = entry.lift(passes.lifter, node)
                passes.borrow.refresh_callee_modes()
                entry.borrow(passes.borrow, node, lifted)
                scratch.leave_body()
            self._merge_new(scratch, seen)

    def _merge_new(self, scratch: Reporter, seen: set) -> None:
        """Hand a copy check's findings to the program reporter, each one time."""
        for diagnostic in in_source_order(scratch.items):
            identity = diagnostic_identity(diagnostic)
            if identity in seen:
                continue
            seen.add(identity)
            self.reporter.items.append(diagnostic)

    def _check_extension_shadows_builtin(self, sites: dict) -> None:
        """Reject an extension method that collides with a built-in (CE2097).

        The question is asked per instance, because the answer can differ: a type with no
        derived `hash()` may take an extension `hash()`. The report is per WRITTEN
        declaration: the copies of one template share its identity and its spans, so
        they are one fault. An `extend T[]` template is judged where it is written, on
        the receiver `T[]`. It is not in the extension table, and no copy of it is ever
        cut for a built-in name, because the built-in answers every call first.
        """
        from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
        from sushi_lang.semantics.generics.extension_targets import (
            ARRAY_BASE_KEY, extension_template_id, written_target)
        from sushi_lang.semantics.generics.monomorphize.order import in_site_order
        from sushi_lang.semantics.generics.type_display import display_type
        from sushi_lang.semantics.generics.types import TypeParameter
        from sushi_lang.semantics.typesys import DynamicArrayType

        collisions: dict = {}

        def collide(target_type, method_name: str, method, template_id, written) -> None:
            stdlib = self._stdlib_method_beside(target_type, method_name, method)
            if stdlib is not None or builtin_method_exists(
                    target_type, method_name, self.tables.derived_methods):
                key = _written_declaration(method, template_id)
                collisions.setdefault(key, []).append(
                    (target_type, method_name, method, written, stdlib))

        for method in list(self.tables.extensions.records()):
            target_type = method.target_type
            collide(target_type, method.name, method, method.template_id,
                    method.template_target or display_type(target_type))

        templates = self.tables.generic_extensions.by_type.get(ARRAY_BASE_KEY, {})
        for (method_name, _key, _unit), template in templates.items():
            receiver = DynamicArrayType(
                base_type=TypeParameter(name=template.type_params[0]))
            decl = template.decl
            written = (written_target(decl.target_type, decl.target_params)
                       if decl is not None and decl.target_type is not None
                       else display_type(receiver))
            collide(receiver, method_name, template,
                    extension_template_id(template), written)

        for found in collisions.values():
            first = in_site_order(
                found, sites, lambda item: getattr(item[0], "name", None),
                lambda item: (display_type(item[0]),))[0]
            self._reject_builtin_shadow(*first)

    def _warn_hidden_imported_extensions(self) -> None:
        """CW3007: a unit's own extension hides an imported public one (C3).

        The own extension wins in its unit, as an own declaration wins over an imported
        flat name, and the warning says so at the declaration. A copy of a template is
        not asked yet: its own template wins in its unit (C5), with no warning.
        """
        from sushi_lang.semantics.generics.type_display import display_type
        from sushi_lang.semantics.visibility import hides_import, warn_hidden_extension

        for own in list(self.tables.extensions.records()):
            if own.template_id is not None or own.unit_name is None:
                continue
            table = self.tables.namespaces.get(own.unit_name)
            scope = table.scope if table is not None else None
            for other in self.tables.extensions.declarations(own.target_type, own.name):
                if hides_import(self.tables.visibility, own, other, scope):
                    warn_hidden_extension(
                        self.reporter, own, other,
                        f"{display_type(own.target_type)}.{own.name}")

    def _stdlib_method_beside(self, target_type, method_name: str, method):
        """The stdlib method on a built-in type that `method` takes the name of, or None.

        C2 for R1 (`docs/design/extension-visibility.md`): a public extension that a
        Sushi-source stdlib module declares on a built-in type is visible in every unit,
        so an extension of its name in another unit could never be called.
        """
        from sushi_lang.semantics.visibility import is_stdlib_builtin_method
        for other in self.tables.extensions.declarations(target_type, method_name):
            if (other.unit_name != method.unit_name
                    and is_stdlib_builtin_method(other, target_type)):
                return other
        return None

    def _reject_builtin_shadow(self, target_type, method_name: str, method,
                               written: str, stdlib=None) -> None:
        """CE2097 for one written declaration, at the instance `target_type`.

        `written` is the target as the source wrote it (`Box@(T)`, `T[]`). `stdlib` is
        the stdlib method it collides with, when a Sushi-source module declares it; the
        note then points at that declaration.
        """
        from sushi_lang.semantics.generics.type_display import display_type

        diagnostic = er.emit_with(
            self.reporter, er.ERR.CE2097,
            method.name_span or method.loc,
            filename=getattr(method, "filename", None),
            name=method_name, type=written,
        )
        if stdlib is not None and stdlib.name_span is not None:
            diagnostic.note_at(
                f"the stdlib module <{stdlib.unit_name}> declares "
                f"'{display_type(target_type)}.{method_name}()' here, and it is "
                "visible in every unit", stdlib.name_span, stdlib.filename)
        else:
            diagnostic.note(
                f"'{display_type(target_type)}.{method_name}()' is defined by the compiler")
        diagnostic.help(
            "a built-in method is always chosen before an extension method, so "
            f"this one could never be called -- rename it, or provide "
            f"'{method_name}()' through a perk implementation "
            f"('extend {written} with <Perk>'), which does "
            "take precedence"
        ).emit()

    def _check_entrypoint(self, compilation_order: list[Unit]) -> None:
        """Main's rule, whole. The ONE home of it (#674).

        It used to have three: the pipeline asked whether a main exists (CE3007) and
        whether a library carries one (CE3501), the collect pass read its return type
        (CE0106), and this pass -- named for main's signature -- checked no signature at
        all and set one bool.

        The order is the ruling's: the entry point exists, a library refuses one, the
        return is a bare integer with no `| E` (docs/design/error-channel.md), and the parameter is `string[] args` -- with no mode, since
        argv is a borrowed view the runtime owns (#844) -- or nothing. The last
        answers `main_expects_args`, which the back end reads to hand argv on.
        """
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
            elif ret_ty is not None and func.err_type is not None:
                er.emit(self.reporter, er.ERR.CE0106,
                        func.err_span or func.ret_span or func.name_span,
                        type=f"{display_type(ret_ty)} | {display_type(func.err_type)}")

        def is_args(param) -> bool:
            return (param.name == "args"
                    and not param.is_nom
                    and isinstance(param.ty, DynamicArrayType)
                    and param.ty.base_type == BuiltinType.STRING)

        if not self.is_library:
            for func in mains:
                wrong = [p for i, p in enumerate(func.params) if i > 0 or not is_args(p)]
                if wrong:
                    er.emit(self.reporter, er.ERR.CE0138,
                            wrong[0].name_span or wrong[0].type_span or func.name_span)

        self.main_expects_args = bool(mains) and [
            is_args(p) for p in mains[0].params] == [True]
