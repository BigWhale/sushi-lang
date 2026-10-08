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
from sushi_lang.internals.diagnostics import SushiError
from sushi_lang.internals.report import Origin, Reporter
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.library_registry import LibraryRegistry, library_unit
from sushi_lang.semantics.library_templates import (
    TemplateSourceError, apply_template_bindings, deserialize_perk_impl,
    parse_one_declaration)
from sushi_lang.semantics.generics.extension_targets import DeclaredTypeNamer
from sushi_lang.semantics.generics.type_display import display_type, display_type_name
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.passes.collect.perks import PerkCollector
from sushi_lang.semantics.visibility import (
    TYPE_KINDS, DeclOrigin, reject_library_clash, warn_shadowed_export)

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import ExtendDef, ExtendWithDef, Program
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


def _slice_label(lib_name: str, what: str) -> str:
    """The file name of a source slice that a library ships: `<template:lib:what>`.

    `what` names the declaration. A type in it is an identity name, and a location shows
    it in the source spelling, `Pair@(T)`.
    """
    from sushi_lang.semantics.generics.type_display import display_type_name
    return f"<template:{lib_name}:{display_type_name(what)}>"


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

    def declared_span(self, table_name: str, name: str) -> Any:
        """Where this snippet declared `name` in `table_name`, or None.

        A flat table only: the span the collector filed for the declaration.
        """
        return getattr(self._tables, table_name).spans.get(name)


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
        # The concrete extension methods a library ships, for the same two readers: the
        # tables, and the backend, which declares each one and never defines it.
        self.shipped_extensions: list[ExtendDef] = []
        # The type names a consumer declaration took from a library (#761, #814, #902):
        # a binary library's export or export closure here, a source library's private
        # unit type or a seeded private template in the collect pass. The library's
        # bodies name them, so the analyzer stops before the per-unit passes measure the
        # library's code against the consumer's layout, or the consumer's code against
        # the library's.
        self.refused_types: list[str] = []
        # The generic type templates the libraries ship, by name: the library of each.
        self.generic_type_owners: dict[str, str] = {}
        # One collector for every re-parsed record, built on first use. A fresh
        # `CollectorPass` rebuilds the predefined type universe each time (#675).
        self._snippet_collector: Optional[CollectorPass] = None
        self._snippet_reporter = Reporter(filename="<library snippet>")

    def _template_origin(self, lib_name: str, version: Optional[str], label: str,
                         source: str) -> Origin:
        """Whose code a library template is, and the text its spans index into.

        The program reporter records the slice under its label, so every location in
        the slice renders its line: the location of a diagnostic and of each note.
        """
        origin = Origin(
            filename=label, source=source,
            provenance=(f"'{lib_name}' {version or 'unknown'} "
                        f"ships this template; it is monomorphized here because of "
                        f"`use <lib/{lib_name}>`"))
        self.reporter.add_slice(label, source)
        return origin

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
                source, f"<perk:{lib_name}:{perk_name}>", lib_name, lib_name,
                f"perk '{perk_name}'")
            perk_def = snippet.declared("perks", perk_name)
            if perk_def is None:
                continue
            perk_table.by_name[perk_name] = perk_def
            perk_table.order.append(perk_name)
            perk_table.library_units[perk_name] = (
                f"lib/{lib_name}/{record.get('unit') or lib_name}")

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
        # A unit with a provenance arrived from a source library or the stdlib.
        consumer_units = {u.name for u in compilation_order if u.provenance is None}
        self._register_types("structs", "struct", LibraryRegistry.get_all_structs,
                             build_units)
        self._register_types("enums", "enum", LibraryRegistry.get_all_enums,
                             build_units)
        self._refile_consumer_extensions(compilation_order)
        self._register_functions(consumer_units)
        self._register_private_functions(build_units)
        self._register_not_exported()
        self._register_constants(compilation_order)
        self._register_private_types(build_units)
        self._register_perk_impls()
        self._register_generic_perk_impls()
        self._reject_foreign_drops(compilation_order)
        self._register_extensions(build_units)
        self._register_conversions()
        self._register_generic_extensions(build_units)
        self._register_generic_functions(build_units, consumer_units)

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

    def _library_file(self, lib_name: str) -> str:
        """The path of the `.slib` a loaded library came from, or its name."""
        manifest = self.linker.loaded_libraries.get(lib_name, {}) if self.linker else {}
        return str(manifest.get("library_path") or lib_name)

    def library_names(self) -> set[str]:
        """The name of every loaded library, as its visibility records carry it."""
        return {lib_name for lib_name, _manifest in self._manifests()}

    def _template_records(self, key: str) -> Iterator[tuple[str, dict, dict]]:
        """Every `templates.<key>` record of every manifest, with its library."""
        for lib_name, manifest in self._manifests():
            templates = manifest.get("templates") or {}
            for record in templates.get(key, []) or []:
                yield lib_name, manifest, record

    def _type_tables(self) -> tuple[dict, dict]:
        """A COPY of the struct and enum tables a manifest type string is parsed against.

        A copy, because the registry UPDATES the dict it is handed with every type it
        parses. Handed the live tables, a library's `Crate` overwrote the consumer's
        declaration of that name in place: the consumer lost its own type with no word
        said, and then heard CE2027 about a field count it never spelled (#739). ONE
        pair for the whole build, so a second library still parses against the first
        library's types, which is what the sharing was for.
        """
        return dict(self.tables.structs.by_name), dict(self.tables.enums.by_name)

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

    def _collect_snippet(self, source: str, label: str, unit: str,
                         lib_name: str, what: str) -> _Snippet:
        """Re-parse one record's source and collect it under `unit`, on the throwaway.

        The one collector for every snippet (#675). Its reporter is a throwaway, so a
        library snippet's diagnostics never reach the consumer's; `label` names the
        snippet there. The unit FILE stays unset, as it was for every fresh collector.

        A source that does not parse, or that is not one declaration, is a fault of the
        library `lib_name` and stops the build with CE3512, which names the library file
        and the record (`what`). It is never reported at the consumer's `use` line.
        """
        try:
            program = parse_one_declaration(source, what, label)
        except TemplateSourceError as e:
            raise SushiError("CE3512", path=self._library_file(lib_name),
                             reason=str(e)) from e
        reporter = self._snippet_reporter
        reporter.source = source
        reporter.filename = label
        if self._snippet_collector is None:
            self._snippet_collector = CollectorPass(reporter)
        tables = self._snippet_collector.run(program, unit_name=unit)
        return _Snippet(program, tables, unit)

    # -- the registry arms ---------------------------------------------------------

    def _register_types(self, key: str, kind: str,
                        exported: Callable[[LibraryRegistry], dict],
                        build_units: set[str]) -> None:
        """Register the concrete structs or enums the libraries export.

        The registry is the one reader of a manifest type record, and there is always
        one: `register()` builds it whenever a library is loaded. A second arm here
        parsed the same records a second way and no build could reach it (#707).

        A name one of THIS build's units already declared is refused here and not
        registered: a type is one name for the whole program.
        """
        table = getattr(self.tables, key)
        for name, ty in exported(self.registry).items():
            if self._reject_type_clash(kind, name, build_units):
                continue
            if name not in table.by_name:
                table.by_name[name] = ty
                table.order.append(name)
        self._record_public_types(key, kind)

    def _record_public_types(self, key: str, kind: str) -> None:
        """File each public type a library exports under the unit that declares it.

        A source library's type has the record of its unit, and a compiled library's
        type has one of the same shape, so it is a name only in a unit whose scope holds
        that unit (#1120). A name a consumer declaration took is refused above and gets
        no record here.
        """
        if self.registry is None:
            return
        for library in self.registry.get_all_libraries().values():
            manifest = library.raw_manifest or {}
            for record in manifest.get(key, []) or []:
                name = record.get("name")
                if not name or name in self.refused_types:
                    continue
                self.tables.visibility.record(DeclOrigin(
                    kind=kind, name=name,
                    unit_name=library_unit(library.name, record.get("unit")),
                    filename=manifest.get("library_path"),
                    is_error=bool(record.get("is_error", False))))

    def _reject_type_clash(self, kind: str, name: str,
                           build_units: set[str]) -> bool:
        """CE3011 when a unit of this build declares a name a library exports (#739).

        Known Limitation 13: a TYPE is one name for the whole program, and identity is
        nominal, so one name is one shape. A SOURCE library's units are ordinary
        compilation units and the collect pass answers this for them, at the
        declaration. A binary library arrives as a manifest that no pass walks, so the
        refusal stands here, where the two declarations first meet.

        CE3011 and not the plain duplicate: the consumer cannot SEE a binary library's
        declaration, so a note has nowhere to point and the head line has to say which
        library holds the name. The consumer's declaration may be of either kind: a
        struct and an enum share one name (#902).
        """
        origin = self._build_declaration(name, build_units)
        if origin is None:
            return False
        reject_library_clash(
            self.reporter,
            DeclOrigin(kind=kind, name=name, unit_name=self._owning_library(kind, name)),
            origin.name_span, kind=origin.word, name=name, filename=origin.filename)
        self.refused_types.append(name)
        return True

    def _build_declaration(self, name: str,
                           build_units: set[str]) -> Optional[DeclOrigin]:
        """The declaration of this type name one of THIS build's units made, or None.

        Either kind answers, because a struct and an enum share one type name. The
        visibility table tells the two sides apart: the consumer's own declaration
        carries one of this build's units, a library's type carries the library.
        """
        for kind in TYPE_KINDS:
            origin = self.tables.visibility.origin(kind, name)
            if origin is not None and origin.unit_name in build_units:
                return origin
        return None

    def _owning_library(self, kind: str, name: str) -> Optional[str]:
        """Which loaded library exports this type, by the name the manifest carries.

        The LAST one, as `get_all_structs` merges them: two libraries exporting one
        name leave the later type in the registry's answer.
        """
        if self.registry is None:
            return None
        exported = "structs" if kind == "struct" else "enums"
        owner = None
        for lib in self.registry.get_all_libraries().values():
            if name in getattr(lib, exported):
                owner = lib.name
        return owner

    def _register_functions(self, consumer_units: set[str]) -> None:
        """Register the libraries' public functions into the function table.

        The registry's `_parse_functions` is the ONE reader of a manifest signature. A
        second builder lived here and read `return_type` alone, so a binary library's
        `| E` was typed StdError at every consumer (#541). A consumer function of the
        same name keeps the name, and CW3002 says so, as for a source library (#1103).

        Each function is also filed under the `lib/<library>/<unit>` that declares it,
        as a source library's is, so it is a name only in a unit whose scope holds that
        unit: its own `use <lib/...>`, or a `public use` chain (#1120). A copy of the
        library's template reads the scope of the library unit, so it calls them too.
        """
        if self.registry is None:
            return
        funcs = self.tables.funcs
        for func_name, func_sig in self.registry.get_all_functions().items():
            existing = funcs.by_name.get(func_name)
            if existing is None:
                funcs.by_name[func_name] = func_sig
                funcs.order.append(func_name)
            elif existing.unit_name in consumer_units:
                self._warn_shadowed_export(existing, self._export_unit(func_name))
        for library in self.registry.get_all_libraries().values():
            manifest = library.raw_manifest or {}
            for record in manifest.get("public_functions", []) or []:
                sig = library.functions.get(record["name"])
                if sig is None:
                    continue
                funcs.by_unit.setdefault(library_unit(library.name, record.get("unit")),
                                         {})[record["name"]] = sig

    def _export_unit(self, func_name: str) -> str:
        """The unit that exports a public function, as `lib/<library>/<unit>`.

        The LAST library that exports the name, as `get_all_functions` merges them.
        """
        owner = func_name
        if self.registry is not None:
            for lib in self.registry.get_all_libraries().values():
                owner = lib.export_units.get(func_name, owner)
        return owner

    def _warn_shadowed_export(self, existing, owner: str) -> None:
        """CW3002 at the consumer declaration that shadows a library export."""
        warn_shadowed_export(self.reporter, existing.name, existing.name_span,
                             existing.filename, owner=owner)

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

        unit = library_unit(lib_name, record.get("unit"))
        existing = constants.by_name.get(const_name)
        if existing is not None:
            if existing.unit_name == unit:
                # The same declaration under a second arm: a public constant that a
                # template body names ships in the closure too.
                return
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

        # Collected under the library unit that declares it, as a source library's
        # constant is: it is a name only where the scope holds that unit (#1120).
        snippet = self._collect_snippet(
            source, f"<const:{lib_name}:{const_name}>", unit, lib_name,
            f"constant '{const_name}'")
        sig = snippet.declared("constants", const_name)
        const_defs = snippet.program.constants or []
        if sig is None or len(const_defs) != 1:
            return

        constants.declare(const_name, sig)
        decl = const_defs[0]
        if record.get("link_symbol") and hasattr(decl, "link_symbol"):
            decl.link_symbol = record["link_symbol"]
        decl.home_unit = record.get("unit") or lib_name
        # The copy lands in a unit of the consumer, and its initializer still reads
        # the library unit's scope.
        decl.scope_unit = unit
        host_unit.ast.constants.append(decl)

    def _register_private_types(self, build_units: set[str]) -> None:
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

        A name one of THIS build's units already declared is refused with CE3011, as
        the public arm refuses it: the transplanted body would otherwise be measured
        against the consumer's layout (#761).
        """
        for lib_name, _manifest, record in self._template_records("private_types"):
            name = record.get("name")
            source = record.get("source")
            if not name or not source:
                continue
            if name in self.tables.structs.by_name or name in self.tables.enums.by_name:
                self._reject_private_type_clash(lib_name, name, build_units)
                continue

            snippet = self._collect_snippet(source, f"<type:{lib_name}:{name}>", lib_name,
                                            lib_name, f"private type '{name}'")
            kind = None
            is_error = False
            for table_name, declared_kind, concrete in _PRIVATE_TYPE_TABLES:
                entry = snippet.declared(table_name, name)
                if entry is None:
                    continue
                kind = declared_kind
                is_error = getattr(entry, "is_error", False)
                if concrete:
                    table = getattr(self.tables, table_name)
                    table.by_name[name] = entry
                    table.order.append(name)
                break
            if kind is None:
                continue

            self.tables.visibility.record(DeclOrigin(
                kind=kind, name=name, unit_name=lib_name, is_public=False,
                is_error=is_error))

    def _reject_private_type_clash(self, lib_name: str, name: str,
                                   build_units: set[str]) -> None:
        """CE3011 when a unit of this build declares a name the closure ships privately.

        The consumer's declaration says which KIND the refused name is: a library's
        private struct takes the name from a consumer enum as firmly as from a struct.
        A name another library already registered is not this build's, and is no clash.
        """
        origin = self._build_declaration(name, build_units)
        if origin is None:
            return
        reject_library_clash(
            self.reporter,
            DeclOrigin(kind=origin.kind, name=name, unit_name=lib_name),
            origin.name_span, kind=origin.word, name=name, filename=origin.filename)
        self.refused_types.append(name)

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
        perks, perk_impls = self.tables.perks, self.tables.perk_impls
        table = self.tables.generic_perk_impls
        is_declared_type = DeclaredTypeNamer(
            structs=self.tables.structs, enums=self.tables.enums,
            generic_structs=self.tables.generic_structs,
            generic_enums=self.tables.generic_enums, perks=perks)

        for lib_name, manifest, record in self._template_records("generic_perk_impls"):
            base = record.get("type")
            perk_name = record.get("perk")
            source = record.get("source")
            if not base or not perk_name or not source:
                continue
            if perk_name not in perks.by_name:
                continue
            if any(t.impl.perk_name == perk_name for t in table.templates(base)):
                continue

            label = _slice_label(lib_name, f"{base} with {perk_name}")
            try:
                program = parse_one_declaration(
                    source, f"perk implementation '{base} with {perk_name}'", label)
            except TemplateSourceError:
                er.emit(self.reporter, er.ERR.CW3506, None, type=base)
                continue
            # The methods are the library's code, as an extension template's are: a
            # copy may call the library's privates, and a diagnostic names the library.
            origin = self._template_origin(lib_name, manifest.get("library_version"),
                                           label, source)
            for impl in [*program.perk_impls, *(program.generic_perk_impls or [])]:
                for method in impl.methods:
                    method.is_library_template = True
                    method.library_origin = origin

            collector = PerkCollector(
                Reporter(source=source, filename=label),
                perks=perks, perk_impls=perk_impls,
                is_declared_type=is_declared_type, generic_perk_impls=table)
            collector.current_unit_name = library_unit(lib_name, record.get("unit"))
            collector.current_unit_file = label
            before = len(table.templates(base))
            collector.collect_implementations(program)
            if len(table.templates(base)) == before:
                # The snippet parsed and the collector still filed nothing: say so, or
                # the user later gets "no such method" on a perk the library implements.
                er.emit(self.reporter, er.ERR.CW3506, None, type=base)

    def _reject_foreign_drops(self, compilation_order: list['Unit']) -> None:
        """CE4012 for a consumer's `Drop` on a type that a binary library declares (#1118).

        The collect pass refuses a foreign `Drop` target from the visibility record, but
        a public library type has a record with no declaring unit, and the consumer's
        implementation is collected before the library's arrives. So the rule is read
        here, where the library's type names and the consumer's implementations meet.
        """
        drop = PerkCollector.DROP_PERK
        files = {u.name: str(u.file_path) for u in compilation_order}
        owners: dict[str, str] = {}
        if self.registry is not None:
            for lib in self.registry.get_all_libraries().values():
                owners.update(dict.fromkeys([*lib.structs, *lib.enums], lib.name))
        for name, lib_name in self.generic_type_owners.items():
            owners.setdefault(name, lib_name)

        perk_impls = self.tables.perk_impls
        found = [(perk_impls.get(name, drop), perk_impls.owner(name, drop))
                 for name in sorted(perk_impls.by_perk.get(drop, ()))]
        table = self.tables.generic_perk_impls
        found += [(t.impl, t.unit_name) for base in sorted(owners)
                  for t in table.templates(base) if t.impl.perk_name == drop]
        for impl, unit_name in found:
            if impl is None or unit_name not in files:
                continue
            target = impl.target_type
            declared = (getattr(target, "base_name", None)
                        or getattr(target, "generic_base", None)
                        or getattr(target, "name", None))
            if declared in owners:
                er.emit(self.reporter, er.ERR.CE4012, impl.perk_name_span or impl.loc,
                        filename=files[unit_name], type=declared, owner=owners[declared])

    def _function_collector(self, reporter: Reporter, unit_name: Optional[str],
                            unit_file: Optional[str]):
        """The collect pass's `FunctionCollector`, over this program's own tables."""
        from sushi_lang.semantics.passes.collect.functions import FunctionCollector

        tables = self.tables
        collector = FunctionCollector(
            reporter, funcs=tables.funcs, generic_funcs=tables.generic_funcs,
            extensions=tables.extensions, generic_extensions=tables.generic_extensions,
            structs=tables.structs, enums=tables.enums,
            generic_structs=tables.generic_structs, generic_enums=tables.generic_enums,
            is_declared_type=DeclaredTypeNamer(
                structs=tables.structs, enums=tables.enums,
                generic_structs=tables.generic_structs,
                generic_enums=tables.generic_enums, perks=tables.perks),
            conversions=tables.conversions)
        collector.visibility = tables.visibility
        collector.current_unit_name = unit_name
        collector.current_unit_file = unit_file
        return collector

    def _refile_consumer_extensions(self, compilation_order: list['Unit']) -> None:
        """File each extension the build writes on a binary library's concrete type.

        The collect pass ran before the library's types were in the tables, so it found
        no target and filed nothing: the method was undefined at every call. The
        library's own extension methods register after this, so a name both declare on
        one type is the duplicate that a source library gives.
        """
        from sushi_lang.semantics.typesys import UnknownType

        if self.registry is None:
            return
        library_types = (set(self.registry.get_all_structs())
                         | set(self.registry.get_all_enums()))
        if not library_types:
            return
        for unit in compilation_order:
            if unit.ast is None:
                continue
            late = [ext for ext in unit.ast.extensions
                    if (isinstance(ext.target_type, UnknownType)
                        and ext.target_type.name in library_types)
                    or (ext.is_conversion and isinstance(ext.ret, UnknownType)
                        and ext.ret.name in library_types)]
            if not late:
                continue
            collector = self._function_collector(
                self.reporter, unit.name, str(unit.file_path))
            refused = {id(ext) for ext in late if not collector.refile_extension(ext)}
            if refused:
                unit.ast.extensions[:] = [ext for ext in unit.ast.extensions
                                          if id(ext) not in refused]

    def _register_extensions(self, build_units: set[str]) -> None:
        """Register the CONCRETE extension methods the libraries ship.

        A record is a signature, because the body is in the library's bitcode: the
        method enters the extension table, and its declaration goes to the backend,
        which declares the symbol and never defines it. The types are read against a
        copy of the tables taken AFTER the private types, so a method on a type the
        export closure ships resolves too.

        A method name the consumer already declares on the same type is CE0101, as for
        a source library: the two bodies would be one symbol.
        """
        from sushi_lang.semantics.ast import Block, ExtendDef, Param
        from sushi_lang.semantics.library_registry import parse_signature
        from sushi_lang.semantics.passes.collect.functions import ExtensionMethod
        from sushi_lang.semantics.type_resolution import parse_type_string

        struct_table, enum_table = self._type_tables()
        extensions = self.tables.extensions
        for lib_name, _manifest, record in self._template_records("extensions"):
            lib_file = self._library_file(lib_name)
            target = parse_type_string(record["type"], struct_table, enum_table)
            sig = parse_signature(record, struct_table, enum_table, lib_path=lib_file)
            existing = extensions.get_method(target, sig.name)
            if existing is not None:
                if existing.unit_name in build_units:
                    self._reject_extension_clash(lib_name, existing.name_span,
                                                 existing.filename, sig.name, target)
                else:
                    self._reject_library_extension_clash(
                        existing.unit_name or "?", lib_name, lib_file, sig.name, target)
                continue

            unit = library_unit(lib_name, record.get("unit"))
            self_mode = record.get("self_mode")
            is_static = bool(record.get("static", False))
            extensions.add_method(ExtensionMethod(
                target_type=target, name=sig.name, ret_type=sig.ret_type,
                params=sig.params, self_mode=self_mode, filename=lib_file,
                unit_name=unit, err_type=sig.err_type, is_static=is_static))
            self.shipped_extensions.append(ExtendDef(
                loc=None, target_type=target, name=sig.name,
                params=[Param(name=p.name, ty=p.ty, is_nom=p.is_nom) for p in sig.params],
                ret=sig.ret_type, body=Block(loc=None, statements=[]),
                self_mode=self_mode,
                err_type=sig.err_type, is_static=is_static))

    def _register_conversions(self) -> None:
        """Register the conversions the libraries ship (docs/design/error-conversion.md 8.2).

        A record is a pair of type names and the symbol of the body, which is in the
        library's bitcode. The pair goes to the conversion table, and a declaration of
        the body goes to the backend, as for an extension method; the conversion is not
        filed in the extension table (ruling P6). The types are read against a copy of
        the tables taken after the private types, as for an extension method.

        Only the unit that declares the target may declare a conversion (C5), so a pair
        that the table holds already means that two libraries declare one target type:
        that clash has its own diagnostic, and the second record adds nothing.
        """
        from sushi_lang.semantics.ast import CONVERSION_METHOD, Block, ExtendDef
        from sushi_lang.semantics.conversions import Conversion, conversion_symbol
        from sushi_lang.semantics.library_registry import manifest_conversions
        from sushi_lang.semantics.type_resolution import parse_type_string
        from sushi_lang.semantics.typesys import EnumType

        struct_table, enum_table = self._type_tables()
        for lib_name, manifest in self._manifests():
            lib_file = self._library_file(lib_name)
            for record in manifest_conversions(manifest):
                source = parse_type_string(record["source"], struct_table, enum_table)
                target = parse_type_string(record["target"], struct_table, enum_table)
                symbol = record["link_symbol"]
                if not (isinstance(source, EnumType) and source.is_error
                        and isinstance(target, EnumType) and target.is_error):
                    raise SushiError(
                        "CE3512", path=lib_file,
                        reason=f"conversion '{record['source']} as {record['target']}' "
                               "does not name two error types")
                if symbol != conversion_symbol(source, target):
                    raise SushiError(
                        "CE3512", path=lib_file,
                        reason=f"conversion '{source.name} as {target.name}' names the "
                               f"symbol '{symbol}'")
                first = self.tables.conversions.file(Conversion(
                    source=source, target=target, symbol=symbol,
                    unit_name=library_unit(lib_name, record.get("unit")),
                    filename=lib_file))
                if first is not None:
                    continue
                self.shipped_extensions.append(ExtendDef(
                    loc=None, target_type=source, name=CONVERSION_METHOD, params=[],
                    ret=target, body=Block(loc=None, statements=[]), self_mode="nom",
                    method_type_args=(target,)))

    def _register_generic_extensions(self, build_units: set[str]) -> None:
        """Register the extension TEMPLATES the libraries ship.

        A template names no instance, so it ships as source: it is re-parsed, its
        calls are bound to the producer's symbols (D4), and the collect pass's own
        `FunctionCollector` files it, so one function files every template. Each copy
        is cut by the analyzer, as for the consumer's own template, and goes home to the
        entry unit, because the template's unit is not a unit of this build.

        A method the consumer already declares on the same target is CE0101, as for a
        source library: the library's copies and the consumer's would be one symbol.
        """
        from sushi_lang.semantics.library_templates import deserialize_extension

        for lib_name, manifest, record in self._template_records("generic_extensions"):
            label = _slice_label(lib_name, f"{record.get('type')} {record.get('name')}")
            try:
                program = deserialize_extension(record, label)
            except TemplateSourceError as e:
                raise SushiError("CE3512", path=self._library_file(lib_name),
                                 reason=str(e)) from e
            source = record.get("source") or ""
            bindings = record.get("bindings") or {}
            for ext in [*program.extensions, *program.generic_extensions]:
                if bindings:
                    apply_template_bindings(ext.body, bindings)
                ext.is_library_template = True
                ext.library_origin = self._template_origin(
                    lib_name, manifest.get("library_version"), label, source)

            reporter = Reporter(source=source, filename=label)
            collector = self._function_collector(
                reporter, library_unit(lib_name, record.get("unit")), label)
            collector.collect_extensions(program)
            if any(d.code == "CE0101" for d in reporter.items):
                self._reject_template_clash(lib_name, record["name"], build_units)

    def _reject_template_clash(self, lib_name: str, method: str,
                               build_units: set[str]) -> None:
        """CE0101 at the template, of the consumer or of another library, that a library
        template of one name meets."""
        other = None
        for declarations in self.tables.generic_extensions.by_type.values():
            for (name, _key), existing in declarations.items():
                if name != method:
                    continue
                if existing.unit_name in build_units:
                    self._reject_extension_clash(lib_name, existing.name_span,
                                                 existing.filename, method, None)
                    return
                if not (existing.unit_name or "").startswith(f"lib/{lib_name}/"):
                    other = existing.unit_name
        if other is not None:
            self._reject_library_extension_clash(
                other, lib_name, self._library_file(lib_name), method, None)

    def _reject_extension_clash(self, lib_name: str, span, filename, method: str,
                                target) -> None:
        """CE0101: the consumer and a binary library declare one extension method.

        The library's declaration has no file the consumer can see, so the note names
        the library in prose, as CE3011 does for a type.
        """
        name = (f"extension method '{method}' for '{display_type(target)}'"
                if target is not None else f"extension method '{method}'")
        er.emit_with(self.reporter, er.ERR.CE0101, span, filename, name=name) \
            .note(f"library '{lib_name}' declares it too") \
            .help("a method is found on the receiver's type, so no alias can choose "
                  "between the two; rename one of them") \
            .emit()

    def _reject_library_extension_clash(self, first_unit: str, lib_name: str,
                                        lib_file: str, method: str, target) -> None:
        """CE0101: two compiled libraries declare one extension method on one type.

        The bodies are two definitions of one symbol, and which one the link keeps
        depends on the build mode, so the second record is refused and not skipped
        (#1120). Neither declaration has a file the consumer can see, so a note names
        each library in prose.
        """
        parts = first_unit.split("/")
        first = parts[1] if len(parts) > 2 and parts[0] == "lib" else first_unit
        name = (f"extension method '{method}' for '{display_type(target)}'"
                if target is not None else f"extension method '{method}'")
        er.emit_with(self.reporter, er.ERR.CE0101, None, lib_file, name=name) \
            .note(f"library '{first}' declares it") \
            .note(f"library '{lib_name}' declares it too") \
            .help("a method is found on the receiver's type, so no import can choose "
                  "between the two; rename one of them") \
            .emit()

    def _register_generic_functions(self, build_units: set[str],
                                    consumer_units: set[str]) -> None:
        """Register the generic function templates the libraries ship."""
        generic_funcs = self.tables.generic_funcs
        for lib_name, manifest, record in self._template_records("generic_functions"):
            func_name = record["name"]
            template_unit = library_unit(lib_name, record.get("unit"))
            existing = generic_funcs.by_name.get(func_name)
            # A CONSUMER's declaration wins, and CW3002 says so (#1103), but an
            # export-closure PRIVATE template must keep its name: shadowing it would
            # change what the library's other bodies call (CE5007). A declaration from
            # ANOTHER library unit is neither: the two coexist, each under its unit
            # (#494/#495), so the walk falls through to registration.
            if existing is not None and \
                    getattr(existing, "unit_name", None) in build_units:
                if record.get("private"):
                    er.emit(self.reporter, er.ERR.CE5007,
                            getattr(existing, "name_span", None),
                            lib=lib_name, name=func_name)
                elif existing.unit_name in consumer_units:
                    self._warn_shadowed_export(existing, template_unit)
                continue
            if generic_funcs.by_unit.get(template_unit, {}).get(func_name):
                continue

            source = record.get("source")
            if not source:
                continue

            # The collector runs under the RECORD's unit, so the template and its
            # instances carry the unit that declared it at the producer (#494).
            label = _slice_label(lib_name, func_name)
            snippet = self._collect_snippet(source, label, template_unit, lib_name,
                                            f"generic function '{func_name}'")
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
            gfd.library_origin = self._template_origin(
                lib_name, manifest.get("library_version"), label, source)
            # The record is the one home of the file a note at the template names: the
            # slice, as the collector of an extension template files it (#1070).
            gfd.filename = label

            # The snippet already carries these, but the record is the source of truth.
            rec_tps = record.get("type_params") or []
            if len(rec_tps) == len(gfd.type_params):
                for tp, rec_tp in zip(gfd.type_params, rec_tps, strict=False):
                    if hasattr(tp, "constraints"):
                        tp.constraints = list(rec_tp.get("constraints") or [])
                    if isinstance(tp, BoundedTypeParam) and "is_pack" in rec_tp:
                        tp.is_pack = bool(rec_tp["is_pack"])

            generic_funcs.declare(func_name, gfd)

    def _register_generic_types(self, key: str) -> None:
        """Register the generic struct or enum templates the libraries ship.

        `key` names both the manifest list and the table: `generic_structs` or
        `generic_enums`.

        A PUBLIC template gets a visibility record under the `lib/<library>/<unit>` that
        declares it, as the registry's other public types have, so the consumer's scope
        admits the name only where it holds that unit (#1120). The seed runs before
        the collect loop, so a consumer that then declares the same name is filed as
        the LOSER, and a rule that reads the winner's shape asks `name_is_contested`
        before it speaks to it (#738). A PRIVATE template is recorded under its
        library, so the collect pass refuses a consumer declaration of that name with
        CE3011, as the private arm does for a concrete type (#814).
        """
        table = getattr(self.tables, key)
        kind = "struct" if key == "generic_structs" else "enum"
        for lib_name, manifest, record in self._template_records(key):
            type_name = record["name"]
            if type_name in table.by_name:
                continue

            source = record.get("source")
            if not source:
                continue

            label = _slice_label(lib_name, type_name)
            snippet = self._collect_snippet(
                source, label, lib_name, lib_name, f"generic {kind} '{type_name}'")
            generic_type = snippet.declared(key, type_name)
            if generic_type is None:
                continue

            # A note at the declaration, a bound of its type parameter included, names
            # the slice and renders its line (#1070). The E3 note of an instance reads
            # the declaration span from the table, as for a template of the program.
            self.reporter.add_slice(label, source)
            table.files[type_name] = label
            table.spans[type_name] = snippet.declared_span(key, type_name)
            table.by_name[type_name] = generic_type
            table.order.append(type_name)
            self.generic_type_owners[type_name] = lib_name
            declarations = snippet.program.structs if kind == "struct" \
                else snippet.program.enums
            node = next((d for d in declarations or [] if d.name == type_name), None)
            shipped_in = manifest.get("library_path")
            is_error = getattr(node, "is_error", False)
            if getattr(node, "is_public", False):
                self.tables.visibility.record(DeclOrigin(
                    kind=kind, name=type_name,
                    unit_name=library_unit(lib_name, record.get("unit")),
                    filename=shipped_in, is_error=is_error))
            else:
                self.tables.visibility.record(DeclOrigin(
                    kind=kind, name=type_name, unit_name=lib_name, filename=shipped_in,
                    is_public=False, is_error=is_error))
