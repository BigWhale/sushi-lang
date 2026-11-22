"""Library manifest generation for .slib files."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.library_templates import (
    doc_record, signature_record, type_string, with_doc,
)
from sushi_lang.semantics.type_predicates import contains_foreign_ptr
from sushi_lang.semantics.ast import Program, VarDef
from sushi_lang.semantics.ast_walk import bodied_with_owner, is_written
from sushi_lang.semantics.passes.collect.dont_panic import marked_name

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer


def own_units(units: list['Unit']) -> list['Unit']:
    """The library's OWN units -- the compilation order minus what the author did not write.

    A `use <collections/iter>` injects the bundled module as an ordinary unit, and a
    `use <lib/other>` over a source library injects its units the same way, so both
    reach the manifest generator alongside the library's own files. A consumer's build
    loads each library and each module of the graph on its own account (#1120), so
    shipping either one's declarations puts a SECOND definition of every name
    into that consumer's build -- CE4001 for a perk, and a duplicate symbol for the
    rest. `Unit.provenance` is the one field that marks such a unit, and it is the
    field `_extract_reexports` reads for the same question about a `public use`, so
    the two take one answer. Every index of the library's DECLARED API reads this filter;
    `dependencies` does not, because it says what a consumer's build must be able to
    provide rather than what this library declares.
    """
    return [u for u in units if u.provenance is None]


def _own_unit_named(path: str, own_names: set[str]) -> str | None:
    """Which of the library's own units a `use "path"` names, or None.

    The written path is main-relative, so it IS the unit name for a library built
    from its own directory. A library unit that imports a sibling through a longer
    path is matched on the last segment, the way the consumer's `_manifest_unit`
    matches an import against the `units` index.
    """
    if path in own_names:
        return path
    tail = path.rsplit("/", 1)[-1]
    matches = [name for name in own_names if name.rsplit("/", 1)[-1] == tail]
    return matches[0] if len(matches) == 1 else None


def _requires_compiler(compiler_version: str) -> str:
    """The constraint a build stamps: the building compiler's minor, when it parses."""
    from sushi_lang.internals.semver import InvalidVersion, Version, default_compiler_req

    try:
        return default_compiler_req(Version.parse(compiler_version))
    except InvalidVersion:
        # `sushi_lang.__version__` falls back to "unknown" when neither the package
        # metadata nor pyproject.toml can be read. Stamp no constraint rather than a
        # wrong one; the load-side check skips an unreadable field.
        return ""


def resolve_library_version(explicit: str | None, library_name: str) -> str:
    """The library's own version: nori.toml when present, else --lib-version (CE3505).

    The nori.toml is the one in the working directory, never one in a parent directory
    or beside the sources. A package IS one version, so that file is the source of truth.
    An explicit flag that contradicts it is rejected rather than silently preferred --
    either way round, a package could otherwise ship under a version it does not claim.
    A nori.toml that exists must be valid: a file that cannot be read is CE3518, and
    every fault that nori's manifest reader refuses is CE3517.
    """
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.internals.semver import InvalidVersion, Version
    from sushi_lang.packager.constants import MANIFEST_NAME
    from sushi_lang.packager.manifest import ManifestError, load_manifest_from_string
    from sushi_lang.packager.paths import find_project_root

    declared: str | None = None
    root = find_project_root()
    if root is not None:
        manifest_path = root / MANIFEST_NAME
        try:
            text = manifest_path.read_bytes()
        except OSError as e:
            raise LibraryError("CE3518", lib=library_name, path=manifest_path,
                               reason=e.strerror or str(e)) from e
        try:
            declared = load_manifest_from_string(text, str(manifest_path)).version
        except ManifestError as e:
            raise LibraryError("CE3517", lib=library_name, reason=str(e),
                               nori_code=e.code) from e

    if declared is not None and explicit is not None and declared != explicit:
        raise LibraryError("CE3505", lib=library_name,
                           reason=f"nori.toml says {declared}, --lib-version says {explicit}")

    chosen = declared if declared is not None else explicit
    if chosen is None:
        raise LibraryError("CE3505", lib=library_name,
                           reason="no nori.toml in the current directory and no --lib-version")

    try:
        Version.parse(chosen)
    except InvalidVersion as e:
        raise LibraryError("CE3505", lib=library_name, reason=str(e)) from e
    return chosen


def _target_name(target_type) -> str | None:
    """The declared type name that a target names: the base of a generic, the element of
    an array."""
    while getattr(target_type, "base_type", None) is not None:
        target_type = target_type.base_type
    return (getattr(target_type, "base_name", None)
            or getattr(target_type, "name", None))


def _private_perk_names(own: list['Unit']) -> set[str]:
    """The perks that the library's own units declare without `public`."""
    return {perk.name for unit in own if unit.ast is not None
            for perk in unit.ast.perks if not perk.is_public}


def _marked_declarations(program: Program) -> Iterator[tuple[str, str]]:
    """`(name, reason)` for each written `dont_panic` declaration of one unit."""
    for _kind, decl, owner in bodied_with_owner(program):
        if is_written(decl) and decl.dont_panic is not None:
            yield marked_name(decl, owner), decl.dont_panic.reason


class LibraryManifestGenerator:
    """Generates .slib library files."""

    def __init__(self, analyzer: 'SemanticAnalyzer'):
        """Initialize manifest generator."""
        self.analyzer = analyzer
        self.structs = analyzer.tables.structs
        self.enums = analyzer.tables.enums
        self._sources: dict[str, str] = {}

    def _source(self, unit: 'Unit') -> str:
        """The unit's source text, read from disk once per generator."""
        text = self._sources.get(unit.name)
        if text is None:
            text = unit.file_path.read_text(encoding="utf-8")
            self._sources[unit.name] = text
        return text

    def source_map(self, units: list['Unit']) -> dict[str, str]:
        """Every own unit's complete source text, keyed by unit name."""
        return {u.name: self._source(u) for u in own_units(units)}

    def generate(self, units: list['Unit'], output_path: Path,
                 library_version: str = "0.0.0",
                 source: dict[str, str] | None = None) -> None:
        """Generate .slib library file."""
        from sushi_lang.internals.version import _get_versions
        from sushi_lang.backend.library_format import LibraryFormat

        VERSION = _get_versions()["app"]

        library_name = output_path.stem

        # The public API is extracted FIRST, because that is what can reject the library
        # (CE5002). A rejected build writes no container at all, and the pipeline turns
        # the reporter's state into the exit code (#436).
        public_functions = self._extract_public_functions(units)
        if self.analyzer.reporter.has_errors:
            return

        manifest = {
            "sushi_lib_version": "3.0",
            "library_name": library_name,
            "library_version": library_version,
            "units": [unit.name for unit in own_units(units)],
            "requires_compiler": _requires_compiler(VERSION),
            "compiler_version": VERSION,
            "public_functions": public_functions,
            "public_constants": self._extract_public_bindings(units, variables=False),
            "public_variables": self._extract_public_bindings(units, variables=True),
            "structs": self._extract_public_types(units, "structs"),
            "enums": self._extract_public_types(units, "enums"),
            "templates": self._extract_templates(units),
            "dependencies": self._extract_dependencies(units),
        }

        # Each conversion a consumer can use (docs/design/error-conversion.md 8.2).
        # Absent when there is none; a reader takes a missing key as an empty list.
        conversions = self._extract_conversions(units)
        if conversions:
            manifest["conversions"] = conversions

        # What each unit hands on (#585). Absent when no unit says `public use`, so
        # an ordinary library grows by nothing. A source library carries the statement
        # in its text as well; the index answers without a parser either way.
        reexports = self._extract_reexports(units)
        if reexports:
            manifest["reexports"] = reexports

        # Each marked declaration with its `because` (docs/design/dont-panic.md, D10),
        # so a reviewer sees the unchecked code before the first build. The library
        # carries the marker in its text as well. Absent when no declaration is marked.
        dont_panic = self._extract_dont_panic(units)
        if dont_panic:
            manifest["dont_panic"] = dont_panic

        # A map beside `units`, not a change to it: `units` is an ordered list and the
        # order is load-bearing for the consumer's injection. Absent when no unit
        # carries a block, so an undocumented library grows by nothing.
        unit_docs = self._extract_unit_docs(units)
        if unit_docs:
            manifest["unit_docs"] = unit_docs

        LibraryFormat.write(output_path, manifest, source=source)

    def _extract_public_functions(self, units: list['Unit']) -> list[dict]:
        """Extract public function signatures from units."""
        import sushi_lang.internals.errors as er

        public_funcs = []

        for unit in own_units(units):
            if unit.ast is None:
                continue
            for func in unit.ast.functions:
                if not func.is_public:
                    continue

                # A generic function is not a concrete callable: it ships only as a
                # template and monomorphizes at the consumer. Emitting one here produced a
                # FuncSig with unresolved type params.
                if func.type_params:
                    continue

                # CE5002: reject foreign `ptr` in a public library signature. The
                # typecheck pass's public-fn ptr fence (CE5008) tests the same condition
                # and exits earlier, so this is the backstop for a direct producer call.
                # A rejection emits and moves on. `generate()` writes nothing once the
                # reporter holds an error, so every bad export is named in one build.
                exposes_ptr = contains_foreign_ptr(func.ret) or any(
                    contains_foreign_ptr(p.ty) for p in func.params
                )
                if exposes_ptr:
                    er.emit(self.analyzer.reporter, er.ERR.CE5002,
                            getattr(func, "name_span", None) or func.loc, name=func.name)
                    continue

                public_funcs.append(with_doc({
                    "name": func.name,
                    "unit": unit.name,
                    **signature_record(func),
                }, func))

        return public_funcs

    def _extract_public_bindings(self, units: list['Unit'], variables: bool) -> list[dict]:
        """The constants, or the unit variables, this library MARKS public.

        `own_units` keeps a bundled stdlib module's declarations out, and an unmarked
        declaration is a decoder detail and not a promise. Each record carries the
        declaration's type (#487).
        """
        constants = self.analyzer.tables.constants
        records = []
        for unit in own_units(units):
            if unit.ast is None:
                continue
            for decl in unit.ast.constants:
                if not decl.is_public or isinstance(decl, VarDef) != variables:
                    continue
                sig = constants.record_of(decl, unit.name)
                if sig is None or sig.const_type is None:
                    raise_internal_error("CE0000", span=decl.loc,
                                         detail=f"the constant '{decl.name}' has no record")
                record = {
                    "name": decl.name,
                    "unit": unit.name,
                    "type": type_string(sig.const_type),
                }
                records.append(with_doc(record, decl))
        return records

    def _extract_public_types(self, units: list['Unit'], key: str) -> list[dict]:
        """The concrete structs (`key` "structs") or enums ("enums") this library MARKS
        public. A generic one ships as a template (`_extract_templates`) instead."""
        members = {"structs": self._struct_members, "enums": self._enum_members}[key]
        records = []
        seen_names = set()
        for unit in own_units(units):
            if unit.ast is None:
                continue
            for decl in getattr(unit.ast, key):
                if decl.type_params or not decl.is_public or decl.name in seen_names:
                    continue
                seen_names.add(decl.name)
                records.append(with_doc({
                    "name": decl.name,
                    "unit": unit.name,
                    **members(decl),
                    "is_generic": False,
                    "type_params": [],
                }, decl))
        return records

    def _extract_conversions(self, units: list['Unit']) -> list[dict]:
        """Every conversion of the library whose TARGET is public, as a pair of names.

        A conversion is as visible as its target type (design 3.8), and C5 puts it in the
        unit that declares the target, so the target is an enum of the same unit. A
        private source is left out: no consumer can hold a value of it.
        """
        kept = self._kept_type_names(own_units(units))
        records = []
        for unit in own_units(units):
            if unit.ast is None:
                continue
            public = {e.name for e in unit.ast.enums if e.is_public}
            for ext in unit.ast.extensions:
                if not ext.is_conversion or ext.target_type is None or ext.ret is None:
                    continue
                source, target = type_string(ext.target_type), type_string(ext.ret)
                if target not in public or source in kept:
                    continue
                records.append(with_doc({
                    "source": source,
                    "target": target,
                    "unit": unit.name,
                }, ext))
        return records

    def _kept_type_names(self, own: list['Unit']) -> set[str]:
        """The private types of the library."""
        kept: set[str] = set()
        for unit in own:
            if unit.ast is None:
                continue
            for decls in (unit.ast.structs, unit.ast.enums):
                kept.update(d.name for d in decls if not d.is_public)
        return kept

    def _struct_members(self, struct_def) -> dict:
        return {"fields": [
            with_doc({"name": f.name, "type": type_string(f.ty)}, f)
            for f in struct_def.fields
        ]}

    def _enum_members(self, enum_def) -> dict:
        variants = []
        for variant in enum_def.variants:
            record = {"name": variant.name, "has_data": bool(variant.associated_types)}
            if variant.associated_types:
                record["data_types"] = [type_string(t)
                                        for t in variant.associated_types]
            variants.append(with_doc(record, variant))
        return {"variants": variants, "is_error": enum_def.is_error}

    def _extract_unit_docs(self, units: list['Unit']) -> dict[str, dict]:
        """Each own unit's own doc block, keyed by unit name.

        `own_units` is the same filter the `units` index and the source section use, so
        the three can never disagree about which units are ours. A bundled stdlib
        module's docs belong to the consumer's own copy of it, not to this library.
        """
        docs: dict[str, dict] = {}
        for unit in own_units(units):
            if unit.ast is None:
                continue
            record = doc_record(unit.ast.doc)
            if record is not None:
                docs[unit.name] = record
        return docs

    def _extract_templates(self, units: list['Unit']) -> dict:
        """The index of the public generics, perks and methods of the library.

        An item is in the index only when a consumer can see it: a private perk, an
        implementation of one, and a method of a private type are not.
        """
        own = own_units(units)
        private_types = self._kept_type_names(own)
        private_perks = _private_perk_names(own)
        generic_functions, generic_structs, generic_enums = self._generic_templates(own)
        templates = {
            "generic_functions": generic_functions,
            "generic_structs": generic_structs,
            "generic_enums": generic_enums,
            "perks": self._declared_perks(own),
            "perk_impls": self._concrete_perk_impls(own, private_types, private_perks),
            "generic_perk_impls": self._generic_perk_impl_templates(
                own, private_types, private_perks),
        }
        # Absent when the library declares no extension method, so a library without
        # one grows by nothing.
        extensions = self._concrete_extensions(own, private_types)
        if extensions:
            templates["extensions"] = extensions
        generic_extensions = self._generic_extension_templates(own, private_types)
        if generic_extensions:
            templates["generic_extensions"] = generic_extensions
        return templates

    def _generic_templates(self, own: list['Unit']):
        """The public generic functions, structs and enums, as templates.

        Every record names the unit that declared it, in the declaration order, unit by
        unit.
        """
        from sushi_lang.semantics.library_templates import (
            serialize_generic_enum, serialize_generic_function, serialize_generic_struct,
        )

        functions: list[dict] = []
        structs: list[dict] = []
        enums: list[dict] = []
        for unit in own:
            if unit.ast is None:
                continue
            rows: tuple[tuple[list[Any], Callable[[Any], dict], list[dict]], ...] = (
                ([f for f in unit.ast.functions if f.is_public and f.type_params],
                 serialize_generic_function, functions),
                ([s for s in unit.ast.structs if s.is_public and s.type_params],
                 serialize_generic_struct, structs),
                ([e for e in unit.ast.enums if e.is_public and e.type_params],
                 serialize_generic_enum, enums),
            )
            for decls, serialize, records in rows:
                for decl in decls:
                    record = serialize(decl)
                    record["unit"] = unit.name
                    records.append(record)
        return functions, structs, enums

    def _generic_extension_templates(self, own: list['Unit'],
                                     private_types: set[str]) -> list[dict]:
        """Every public extension TEMPLATE of the library's own units on a public type."""
        from sushi_lang.semantics.library_templates import serialize_generic_extension

        records: list[dict] = []
        for unit in own:
            if unit.ast is None:
                continue
            for ext in unit.ast.generic_extensions or []:
                if not ext.is_public or _target_name(ext.target_type) in private_types:
                    continue
                record = serialize_generic_extension(ext)
                record["unit"] = unit.name
                records.append(record)
        return records

    def _concrete_extensions(self, own: list['Unit'],
                             private_types: set[str]) -> list[dict]:
        """Every public CONCRETE extension method of the library on a public type."""
        from sushi_lang.semantics.library_templates import serialize_extension

        records: list[dict] = []
        for unit in own:
            if unit.ast is None:
                continue
            for ext in unit.ast.extensions:
                # A conversion is no method of its source (ruling P6); it is in the
                # manifest's `conversions` list.
                if (ext.is_conversion or not ext.is_public
                        or _target_name(ext.target_type) in private_types):
                    continue
                record = serialize_extension(ext)
                record["unit"] = unit.name
                records.append(record)
        return records

    def _generic_perk_impl_templates(self, own: list['Unit'], private_types: set[str],
                                     private_perks: set[str]) -> list[dict]:
        """Every generic-target implementation of a public perk on a public type (#543)."""
        from sushi_lang.semantics.library_templates import serialize_generic_perk_impl
        from sushi_lang.semantics.passes.collect.perks import covering_base

        records: list[dict] = []
        for unit in own:
            if unit.ast is None:
                continue
            for impl in getattr(unit.ast, "generic_perk_impls", None) or []:
                base = covering_base(impl.target_type)
                if (base is None or base in private_types
                        or impl.perk_name in private_perks):
                    continue
                record = serialize_generic_perk_impl(impl)
                record["unit"] = unit.name
                records.append(record)
        return records

    def _declared_perks(self, own: list['Unit']) -> list[dict]:
        """Every public perk DEFINITION of the library's own units."""
        from sushi_lang.semantics.library_templates import serialize_perk

        records: list[dict] = []
        for unit in own:
            if unit.ast is None:
                continue
            for perk in unit.ast.perks:
                if not perk.is_public:
                    continue
                record = serialize_perk(perk)
                record["unit"] = unit.name
                records.append(record)
        return records

    def _concrete_perk_impls(self, own: list['Unit'], private_types: set[str],
                             private_perks: set[str]) -> list[dict]:
        """Every written concrete implementation of a public perk on a public type.

        A predefined perk is public.

        A monomorphized copy of a generic-target implementation joins `perk_impls`, and
        it is not written: its template is in `generic_perk_impls`.
        """
        from sushi_lang.semantics.library_templates import serialize_perk_impl
        from sushi_lang.semantics.passes.collect.perks import perk_type_name
        from sushi_lang.semantics.generics.types import GenericTypeRef

        records: list[dict] = []
        for unit in own:
            if unit.ast is None:
                continue
            for impl in unit.ast.perk_impls:
                if not is_written(impl) or isinstance(impl.target_type, GenericTypeRef):
                    continue
                if (perk_type_name(impl.target_type) is None
                        or impl.perk_name in private_perks
                        or _target_name(impl.target_type) in private_types):
                    continue
                record = serialize_perk_impl(impl)
                record["unit"] = unit.name
                records.append(record)
        return records

    def _extract_reexports(self, units: list['Unit']) -> list[dict]:
        """What each own unit RE-EXPORTS: one record per `public use` (#585).

        `public use X` makes X's public names the unit's own, so the unit's importers
        get them where its own names land (`docs/design/unit-namespaces.md` section
        8.1). The consumer's `namespaces` pass reads the statement in the text like any
        other; the record is for a reader with no parser (`--lib-info`).

        A record is the TARGET and the unit that wrote it. `kind` is which of the three
        producers the target is, so the consumer picks the same provider builder the
        written statement would have picked: a sibling unit of this library, a stdlib
        module, or another library. A unit target is resolved to the name the `units`
        index carries, because that is the key the consumer looks a unit up by; the
        other two travel as the written path, which is what names them everywhere.

        Own units only (#594). A bundled stdlib module and an injected source library
        both re-export on their own account, and the consumer reads their statements
        from their own text.
        """
        own = own_units(units)
        own_names = {unit.name for unit in own}
        records = []
        for unit in own:
            if unit.ast is None:
                continue
            for use_stmt in unit.ast.uses or ():
                if not use_stmt.is_public:
                    continue
                if use_stmt.is_stdlib:
                    kind, path = "stdlib", use_stmt.path
                elif use_stmt.is_library:
                    kind, path = "library", use_stmt.path
                else:
                    sibling = _own_unit_named(use_stmt.path, own_names)
                    if sibling is None:
                        # A `public use` of a unit this library does not own cannot
                        # happen: the path resolved to a unit for the build to have
                        # got here. Recording nothing is still better than
                        # recording a name the consumer cannot look up.
                        continue
                    kind, path = "unit", sibling
                records.append({"unit": unit.name, "path": path, "kind": kind})
        return records

    def _extract_dont_panic(self, units: list['Unit']) -> list[dict]:
        """Every `dont_panic` declaration of the library's own units, with its reason.

        A written declaration only: a monomorphized copy carries the marker of its
        template, and the template is the record. The name is the one a reader finds
        the declaration by: `name` for a function, `Type.name` for a method or a static,
        and `Source as Target` for a conversion.
        """
        return [{"unit": unit.name, "name": name, "reason": reason}
                for unit in own_units(units) if unit.ast is not None
                for name, reason in _marked_declarations(unit.ast)]

    def _extract_dependencies(self, units: list['Unit']) -> list[dict]:
        """What a consumer's build must load: one record per module and per library.

        A `stdlib` record is a module that a unit of the build uses. A `library` record
        is a `use <lib/...>` that one of the library's OWN units writes, plain or public
        (#1120), with the name and the version of the `.slib` that this build found for
        the path: the consumer loads the whole graph, and a second version of one
        library in it is CE3519. An injected source library records its own uses.

        `units` names the library's own units that write the `use` (#1123). The load
        and the version check read the record whole; the consumer gives each unit of
        the library only the records that name it. A module that only a unit the
        library does not own uses has an empty list.

        A module of the methods of the built-in types is recorded when the build holds
        it, also with no `use`: such a method needs no import, and a template that calls
        one is checked again in the consumer's build (`extension-visibility.md` R1).
        """
        from sushi_lang.semantics.stdlib_registry import BUILTIN_TYPE_METHOD_MODULES
        modules: dict[str, set[str]] = {
            unit.name: set() for unit in units
            if unit.is_bundled_stdlib and unit.name in BUILTIN_TYPE_METHOD_MODULES}
        libraries: dict[str, dict] = {}
        writers: dict[str, set[str]] = {}
        own = {unit.name for unit in own_units(units)}

        for unit in units:
            if unit.ast is None:
                continue
            for use_stmt in unit.ast.uses:
                if use_stmt.is_stdlib:
                    users = modules.setdefault(use_stmt.path, set())
                    if unit.name in own:
                        users.add(unit.name)
                elif use_stmt.is_library and unit.name in own:
                    writers.setdefault(use_stmt.path, set()).add(unit.name)
                    if use_stmt.path not in libraries:
                        libraries[use_stmt.path] = {
                            "path": use_stmt.path, "kind": "library",
                            "library_name": use_stmt.library_name or "",
                            "library_version": use_stmt.library_version or ""}

        return ([{"path": path, "kind": "stdlib", "units": sorted(modules[path])}
                 for path in sorted(modules)]
                + [{**libraries[path], "units": sorted(writers[path])}
                   for path in sorted(libraries)])
