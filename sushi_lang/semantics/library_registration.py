"""The `libraries` step: what a loaded BINARY `.slib` declares enters the tables.

A SOURCE library never reaches this module: its units join the consumer's build and
the ordinary passes compile them (`docs/design/libraries.md` section 4). A binary
library arrives as a manifest, and everything it declares is registered from that
manifest here: the typed API records through the `LibraryRegistry`, and everything
that has to be re-parsed (a template, a perk, a constant, a private type) through ONE
throwaway collector over the record's source text.

The analyzer decides WHEN each step runs (`SemanticAnalyzer.check()` is the authority
on the pass order). This module owns the registration and nothing about the order.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional, Protocol

import sushi_lang.internals.errors as er
from sushi_lang.internals.report import Origin, Reporter
from sushi_lang.semantics.library_registry import LibraryRegistry
from sushi_lang.semantics.library_templates import (
    apply_template_bindings, deserialize_perk_impl)
from sushi_lang.semantics.generics.extension_targets import DeclaredTypeNamer
from sushi_lang.semantics.generics.type_display import display_type_name
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.passes.collect.perks import PerkCollector
from sushi_lang.semantics.visibility import DeclOrigin

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import ExtendWithDef, Program
    from sushi_lang.semantics.passes.collect.functions import FuncSig
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.units import Unit


class LoadedLibraries(Protocol):
    """What this module reads off the backend's library resolver: its manifests.

    A Protocol and not the class, because semantics must not import backend.
    """
    loaded_libraries: dict[str, dict]


# Where a private type the export closure ships can land in the snippet's tables, what
# KIND of declaration each landing makes it, and whether this arm registers it. A
# CONCRETE type is registered here; a template is registered from the manifest's own
# `generic_structs` / `generic_enums` arm, so only its kind is read here.
_PRIVATE_TYPE_TABLES = (
    ("structs", "struct", True),
    ("enums", "enum", True),
    ("generic_structs", "struct", False),
    ("generic_enums", "enum", False),
)


class _Snippet:
    """One re-parsed manifest record: its AST, and what the shared collector filed.

    The collector's tables accumulate across snippets, so a name is read back the way
    a fresh collector answered it. A unit-keyed table (a constant, a generic function)
    answers from the snippet's own unit, because two library units may each ship one
    `helper` (#494) and the flat view keeps only the first. A flat table answers by
    name: a type is one name for the whole program, so a second snippet declaring one
    is the same declaration shipped under a second arm -- a private generic struct
    travels as a private type AND as a template -- and the first collection is it.
    """

    def __init__(self, program: 'Program', tables: 'SymbolTables', unit: str) -> None:
        self.program = program
        self._tables = tables
        self._unit = unit

    def declared(self, table_name: str, name: str) -> Any:
        """What this snippet declared under `name` in `table_name`, or None."""
        table = getattr(self._tables, table_name)
        by_unit = getattr(table, "by_unit", None)
        if by_unit is not None:
            return by_unit.get(self._unit, {}).get(name)
        return table.by_name.get(name)


class LibraryRegistration:
    """Registers what the loaded binary libraries declare into one program's tables.

    Built once per analysis with the program reporter, the whole-program `SymbolTables`
    the collect loop fills, the backend's library resolver (held through a Protocol)
    and the `LibraryRegistry`, which `register()` builds when it was not handed one.

    Two readers come back later: `signatures()` for the instantiate pass, and
    `kept_constant_names()` for the scope pass.
    """

    def __init__(self, reporter: Reporter, tables: 'SymbolTables',
                 linker: Optional[LoadedLibraries],
                 registry: Optional[LibraryRegistry]) -> None:
        self.reporter = reporter
        self.tables = tables
        self.linker = linker
        self.registry = registry
        # The concrete perk implementations a library ships: registered here for the
        # constraint checks and dispatch, declared and never defined by the backend.
        self.shipped_perk_impls: list['ExtendWithDef'] = []
        # One collector for every re-parsed record, built on first use. A fresh
        # `CollectorPass` rebuilds the predefined type universe each time (#675).
        self._snippet_collector: Optional[CollectorPass] = None
        self._snippet_reporter = Reporter(filename="<library snippet>")

    # -- the entry points the analyzer calls --------------------------------------

    def seed_perks(self, perk_table) -> None:
        """Seed the perk DEFINITIONS the libraries ship into `perk_table`.

        Called BEFORE the collect loop: perk-impl collection validates each impl
        against the visible definitions (CE4003), so the contract must already be
        there. The analyzer names the table, as it names the moment: this module owns
        the registration and nothing about the order.
        """
        if perk_table is None:
            return
        for lib_name, _manifest, record in self._template_records("perks"):
            perk_name = record.get("name")
            if not perk_name or perk_name in perk_table.by_name:
                continue
            source = record.get("source")
            if not source:
                continue
            snippet = self._collect_snippet(
                source, f"<perk:{lib_name}:{perk_name}>", lib_name)
            perk_def = snippet.declared("perks", perk_name)
            if perk_def is None:
                continue
            perk_table.by_name[perk_name] = perk_def
            perk_table.order.append(perk_name)

    def seed_generic_types(self) -> None:
        """Seed the generic struct and enum TEMPLATES the libraries ship (#728).

        Called BEFORE the collect loop, for `seed_perks`' reason one kind further on. A
        consumer writes `extend Crate@(T)` and `extend Crate@(i32)`, and the collect
        pass reads that base while it collects the unit, so the library's generic has
        to be in the tables by then. Registered after the loop it was not, and the
        target was refused with a CE2001 for a type the program does have -- the
        concrete spelling since #698, the template spelling since the base check was
        added to it.

        The library's own re-parsed templates read the same tables
        (`_register_generic_perk_impls`), so one seam answers both readers and the
        registration runs once.
        """
        self._register_generic_types("generic_structs")
        self._register_generic_types("generic_enums")

    def register(self, compilation_order: list['Unit']) -> None:
        """Register everything but the perk definitions, in the order the tables need.

        Structs before enums, because an enum payload may name a struct. The API
        before the export closure's private helpers and constants, whose clash with a
        consumer name is CE5007 (local-wins would silently change what the library's
        monomorphized bodies call), and then the names the library keeps, which are
        names and not callables (#469). Perk IMPLEMENTATIONS after the consumer's own,
        so local wins, and before `instantiate`, so the constraint validator sees them.

        The generic TYPE templates are NOT here: `seed_generic_types` files them before
        the collect loop, because an extension target names one and the collect pass
        reads that name (#728). They are in the tables by the time anything below asks.
        """
        if self.linker is not None and self.registry is None:
            self._build_registry()
        if self.registry is None and self.linker is None:
            return
        build_units = {u.name for u in compilation_order}
        self._register_types("structs", LibraryRegistry.get_all_structs)
        self._register_types("enums", LibraryRegistry.get_all_enums)
        self._register_functions()
        self._register_private_functions(build_units)
        self._register_not_exported()
        self._register_constants(compilation_order)
        self._register_private_types()
        self._register_perk_impls()
        self._register_generic_perk_impls()
        self._register_generic_functions(build_units)

    def signatures(self) -> Iterator['FuncSig']:
        """Every signature a loaded library's manifest declares: the public API, and
        the export closure's private helpers."""
        if self.registry is None:
            return
        yield from self.registry.get_all_functions().values()
        for _lib, sig in self.registry.get_all_private_functions().values():
            yield sig

    def kept_constant_names(self) -> frozenset[str]:
        """The constants a library declares and does not export, by name.

        The scope pass asks, so a mention reaches the type pass and hears whose the
        declaration is instead of hearing that there is none (#487).
        """
        kept = self.tables.library_not_exported
        return frozenset(name for name, (_lib, kind) in kept.items()
                         if kind == "constant")

    # -- the manifests ------------------------------------------------------------

    def _manifests(self) -> Iterator[tuple[str, dict]]:
        """Every loaded library's manifest, by library name. Nothing without a linker."""
        if self.linker is None:
            return
        yield from self.linker.loaded_libraries.items()

    def _template_records(self, key: str) -> Iterator[tuple[str, dict, dict]]:
        """Every `templates.<key>` record of every manifest, with its library."""
        for lib_name, manifest in self._manifests():
            templates = manifest.get("templates") or {}
            for record in templates.get(key, []) or []:
                yield lib_name, manifest, record

    def _type_tables(self) -> tuple[dict, dict]:
        """The struct and enum tables a manifest type string is parsed against."""
        return self.tables.structs.by_name, self.tables.enums.by_name

    def _build_registry(self) -> None:
        """Build the `LibraryRegistry` from the loaded manifests."""
        self.registry = LibraryRegistry()
        struct_table, enum_table = self._type_tables()
        for lib_name, manifest in self._manifests():
            lib_path = Path(manifest.get("library_path", lib_name))
            self.registry.register_library(
                lib_path=lib_path,
                manifest=manifest,
                struct_table=struct_table,
                enum_table=enum_table,
            )

    # -- one collector for every snippet -------------------------------------------

    def _collect_snippet(self, source: str, label: str, unit: str) -> _Snippet:
        """Re-parse one record's source and collect it under `unit`, on the throwaway.

        The one collector for every snippet (#675). Its reporter is a throwaway, so a
        library snippet's diagnostics never reach the consumer's; `label` names the
        snippet there. The unit FILE stays unset, as it was for every fresh collector.
        """
        from sushi_lang.internals.parser import parse_to_ast

        program, _tree = parse_to_ast(source)
        reporter = self._snippet_reporter
        reporter.source = source
        reporter.filename = label
        if self._snippet_collector is None:
            self._snippet_collector = CollectorPass(reporter)
        tables = self._snippet_collector.run(program, unit_name=unit)
        return _Snippet(program, tables, unit)

    # -- the registry arms ---------------------------------------------------------

    def _register_types(self, key: str, exported: Callable[[LibraryRegistry], dict]) -> None:
        """Register the concrete structs or enums the libraries export.

        The registry is the one reader of a manifest type record, and there is always
        one: `register()` builds it whenever a library is loaded. A second arm here
        parsed the same records a second way and no build could reach it (#707).
        """
        table = getattr(self.tables, key)
        for name, ty in exported(self.registry).items():
            if name not in table.by_name:
                table.by_name[name] = ty
                table.order.append(name)

    def _register_functions(self) -> None:
        """Register the libraries' public functions into the function table.

        The registry's `_parse_functions` is the ONE reader of a manifest signature. A
        second builder lived here and read `return_type` alone, so a binary library's
        `| E` was typed StdError at every consumer (#541).
        """
        if self.registry is None:
            return
        funcs = self.tables.funcs
        for func_name, func_sig in self.registry.get_all_functions().items():
            if func_name not in funcs.by_name:
                funcs.by_name[func_name] = func_sig
                funcs.order.append(func_name)

    def _register_private_functions(self, build_units: set[str]) -> None:
        """Register the export-closure private helpers (C4b/C5).

        Each record registers under ITS unit, so two of the library's own units may
        each ship a private `helper` (#494). The CE5007 clash is measured against the
        CONSUMER's own declarations -- a record from another library unit is a
        coexisting declaration, not a clash. Each signature also registers under its
        LINK SYMBOL, which is the name a template body's bindings rewrite calls to
        (D4): `$` lies outside every user name's alphabet, so the alias can collide
        with nothing.
        """
        if self.registry is None:
            return
        funcs = self.tables.funcs
        for (_unit, name), (lib_name, sig) in \
                self.registry.get_all_private_functions().items():
            existing = funcs.by_name.get(name)
            if existing is not None and getattr(existing, "unit_name", None) in build_units:
                er.emit(self.reporter, er.ERR.CE5007,
                        getattr(existing, "name_span", None),
                        lib=lib_name, name=name)
                continue
            funcs.declare(name, sig)
            link_symbol = getattr(sig, "link_symbol", None)
            if link_symbol:
                funcs.declare(link_symbol, sig)

    def _register_not_exported(self) -> None:
        """Record what a loaded library declares and does not export (#469)."""
        if self.registry is None:
            return
        self.tables.library_not_exported.update(self.registry.get_all_not_exported())

    # -- the re-parsed arms --------------------------------------------------------

    def _register_constants(self, compilation_order: list['Unit']) -> None:
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
        host_unit = next(
            (u for u in compilation_order if u.ast is not None), None
        )
        if host_unit is None:
            return

        for lib_name, manifest in self._manifests():
            templates = manifest.get("templates") or {}
            for record in manifest.get("public_constants", []) or []:
                self._register_one_constant(host_unit, lib_name, record, published=True)
            # A unit variable's record carries the symbol of the storage the library
            # defines; the consumer's backend declares it under that name.
            for record in manifest.get("public_variables", []) or []:
                self._register_one_constant(host_unit, lib_name, record, published=True)
            for record in templates.get("constants", []) or []:
                self._register_one_constant(host_unit, lib_name, record, published=False)

    def _register_one_constant(self, host_unit, lib_name: str, record: dict,
                               *, published: bool) -> None:
        """Re-parse one constant record and register it under its own name."""
        constants = self.tables.constants
        const_name = record.get("name")
        source = record.get("source")
        if not const_name or not source:
            # A manifest written before constants carried their source. The name and the
            # type are still printed by `--lib-info`; only the value is out of reach.
            return

        existing = constants.by_name.get(const_name)
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

        snippet = self._collect_snippet(
            source, f"<const:{lib_name}:{const_name}>", lib_name)
        sig = snippet.declared("constants", const_name)
        const_defs = snippet.program.constants or []
        if sig is None or len(const_defs) != 1:
            return

        constants.by_name[const_name] = sig
        constants.order.append(const_name)
        decl = const_defs[0]
        if record.get("link_symbol") and hasattr(decl, "link_symbol"):
            decl.link_symbol = record["link_symbol"]
        host_unit.ast.constants.append(decl)

    def _register_private_types(self) -> None:
        """Register the private structs and enums the export closure ships.

        A private type is not in the manifest's `structs` / `enums` index -- the marker
        gates that -- so it travels as source beside the closure's private constants, and
        it is re-parsed here for the same reason: a monomorphized template body names it.

        The visibility table gets a PRIVATE record for each, so the consumer's own code
        cannot name it while the transplanted body can (#468).

        A GENERIC private type travels under two arms: here as source, and in the
        `generic_structs` / `generic_enums` index, which is what `_register_generic_types`
        reads. Only the record is this arm's, and the SNIPPET says which kind it is --
        reading the concrete tables answered "enum" for a generic struct, because a
        template lands in neither of them (#707).
        """
        for lib_name, _manifest, record in self._template_records("private_types"):
            name = record.get("name")
            source = record.get("source")
            if not name or not source:
                continue
            if name in self.tables.structs.by_name or name in self.tables.enums.by_name:
                continue

            snippet = self._collect_snippet(source, f"<type:{lib_name}:{name}>", lib_name)
            kind = None
            for table_name, declared_kind, concrete in _PRIVATE_TYPE_TABLES:
                entry = snippet.declared(table_name, name)
                if entry is None:
                    continue
                kind = declared_kind
                if concrete:
                    table = getattr(self.tables, table_name)
                    table.by_name[name] = entry
                    table.order.append(name)
                break
            if kind is None:
                continue

            self.tables.visibility.record(DeclOrigin(
                kind=kind, name=name, unit_name=lib_name, is_public=False))

    def _register_perk_impls(self) -> None:
        """Register the concrete perk IMPLEMENTATIONS the libraries ship."""
        perks, perk_impls = self.tables.perks, self.tables.perk_impls
        for _lib_name, _manifest, record in self._template_records("perk_impls"):
            type_name = record.get("type")
            perk_name = record.get("perk")
            if not type_name or not perk_name:
                continue
            if perk_name not in perks.by_name:
                continue
            if perk_impls.implements(type_name, perk_name):
                continue
            # CE4007 interplay: skip on a method-name clash with a local extension
            # method on the same type.
            existing_methods = self.tables.extensions.by_type.get(type_name, {})
            method_names = [m.get("name") for m in record.get("methods", []) or []]
            if any(name in existing_methods for name in method_names):
                continue

            try:
                impl = deserialize_perk_impl(record)
            except Exception:
                # The snippet failed to re-parse; skip rather than crash the consumer
                # build (it can supply its own impl) -- but say so, or the user later
                # gets "no such method" on a perk the library implements.
                er.emit(self.reporter, er.ERR.CW3506, None,
                        type=display_type_name(type_name))
                continue

            if perk_impls.register(impl, type_name):
                self.shipped_perk_impls.append(impl)

    def _register_generic_perk_impls(self) -> None:
        """Register the generic-target perk implementation TEMPLATES the libraries ship (#543).

        `extend Box@(T) with Show` names no instantiation, so it is a template: one copy
        per instantiation of `Box` is cut by the analyzer, exactly as for a consumer's
        own. The re-parsed source goes through the collect pass's own `PerkCollector`,
        so the one function that files every template files this one too, under the
        unit that declared it at the producer. A template already in the table for the
        same target and perk -- the consumer's own, collected first -- wins, as a
        consumer's concrete implementation does.
        """
        from sushi_lang.internals.parser import parse_to_ast

        perks, perk_impls = self.tables.perks, self.tables.perk_impls
        table = self.tables.generic_perk_impls
        is_declared_type = DeclaredTypeNamer(
            structs=self.tables.structs, enums=self.tables.enums,
            generic_structs=self.tables.generic_structs,
            generic_enums=self.tables.generic_enums, perks=perks)

        for lib_name, _manifest, record in self._template_records("generic_perk_impls"):
            base = record.get("type")
            perk_name = record.get("perk")
            source = record.get("source")
            if not base or not perk_name or not source:
                continue
            if perk_name not in perks.by_name:
                continue
            if any(t.impl.perk_name == perk_name for t in table.templates(base)):
                continue

            label = f"<template:{lib_name}:{base} with {perk_name}>"
            try:
                program, _tree = parse_to_ast(source)
            except Exception:
                er.emit(self.reporter, er.ERR.CW3506, None, type=base)
                continue

            collector = PerkCollector(
                Reporter(source=source, filename=label),
                perks=perks, perk_impls=perk_impls,
                is_declared_type=is_declared_type, generic_perk_impls=table)
            collector.current_unit_name = f"lib/{lib_name}/{record.get('unit') or lib_name}"
            collector.current_unit_file = label
            before = len(table.templates(base))
            collector.collect_implementations(program)
            if len(table.templates(base)) == before:
                # The snippet parsed and the collector still filed nothing: say so, or
                # the user later gets "no such method" on a perk the library implements.
                er.emit(self.reporter, er.ERR.CW3506, None, type=base)

    def _register_generic_functions(self, build_units: set[str]) -> None:
        """Register the generic function templates the libraries ship."""
        generic_funcs = self.tables.generic_funcs
        for lib_name, manifest, record in self._template_records("generic_functions"):
            func_name = record["name"]
            template_unit = f"lib/{lib_name}/{record.get('unit') or lib_name}"
            existing = generic_funcs.by_name.get(func_name)
            # A CONSUMER's declaration wins silently, but an export-closure PRIVATE
            # template must keep its name: shadowing it would change what the
            # library's other bodies call (CE5007). A declaration from ANOTHER library
            # unit is neither: the two coexist, each under its unit (#494/#495), so the
            # walk falls through to registration.
            if existing is not None and \
                    getattr(existing, "unit_name", None) in build_units:
                if record.get("private"):
                    er.emit(self.reporter, er.ERR.CE5007,
                            getattr(existing, "name_span", None),
                            lib=lib_name, name=func_name)
                continue
            if generic_funcs.by_unit.get(template_unit, {}).get(func_name):
                continue

            source = record.get("source")
            if not source:
                continue

            # The collector runs under the RECORD's unit, so the template and its
            # instances carry the unit that declared it at the producer (#494).
            label = f"<template:{lib_name}:{func_name}>"
            snippet = self._collect_snippet(source, label, template_unit)
            gfd = snippet.declared("generic_funcs", func_name)
            if gfd is None:
                continue

            # D4: bind every free name the producer resolved to its symbol, before any
            # instance is cut from this body.
            bindings = record.get("bindings") or {}
            if bindings:
                apply_template_bindings(gfd.body, bindings)

            gfd.is_library_template = True
            # Whose code the body is, and what text its spans index into. The
            # collector above already reports against the slice; the per-unit passes
            # check the MONOMORPHIZED instance and need the same answer, or they render
            # a library's mistake against the consumer's file (#471). The filename
            # shape is the throwaway reporter's, so one convention names a template
            # everywhere.
            gfd.library_origin = Origin(
                filename=label,
                source=source,
                provenance=(
                    f"'{lib_name}' {manifest.get('library_version') or 'unknown'} "
                    f"ships this template; it is monomorphized here because of "
                    f"`use <lib/{lib_name}>`"),
            )

            # The snippet already carries these, but the record is the source of truth.
            rec_tps = record.get("type_params") or []
            if len(rec_tps) == len(gfd.type_params):
                for tp, rec_tp in zip(gfd.type_params, rec_tps, strict=False):
                    if hasattr(tp, "constraints"):
                        tp.constraints = list(rec_tp.get("constraints") or [])
                    if hasattr(tp, "is_pack") and "is_pack" in rec_tp:
                        tp.is_pack = bool(rec_tp["is_pack"])

            generic_funcs.declare(func_name, gfd)

    def _register_generic_types(self, key: str) -> None:
        """Register the generic struct or enum templates the libraries ship.

        `key` names both the manifest list and the table: `generic_structs` or
        `generic_enums`.
        """
        table = getattr(self.tables, key)
        for lib_name, _manifest, record in self._template_records(key):
            type_name = record["name"]
            if type_name in table.by_name:
                continue

            source = record.get("source")
            if not source:
                continue

            snippet = self._collect_snippet(
                source, f"<template:{lib_name}:{type_name}>", lib_name)
            generic_type = snippet.declared(key, type_name)
            if generic_type is None:
                continue

            table.by_name[type_name] = generic_type
            table.order.append(type_name)
