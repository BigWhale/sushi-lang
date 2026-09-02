"""Library manifest generation for .slib files."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sushi_lang.semantics.library_templates import (
    doc_record, signature_record, type_string, with_doc,
)
from sushi_lang.semantics.unit_symbols import mangle_unit_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer


def own_units(units: list['Unit']) -> list['Unit']:
    """The library's OWN units -- the compilation order minus what came bundled.

    A `use <collections/iter>` injects the bundled module as an ordinary unit, so it
    reaches the manifest generator alongside the library's own files. The consumer has
    its own copy of every bundled module, so shipping ours would put a second
    definition of each of its symbols into their build. One filter, used by both the
    `units` index and the source section, so the two can never disagree.
    """
    from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES

    return [u for u in units if u.name not in SOURCE_STDLIB_MODULES]


def collect_unit_source(units: list['Unit']) -> dict[str, str]:
    """Read every own unit's complete source text, keyed by unit name.

    Whole files, not the per-declaration slices the binary path ships: a source library
    has no export closure to compute, because there is nothing to leave out.
    """
    return {u.name: u.file_path.read_text(encoding="utf-8") for u in own_units(units)}


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


def resolve_library_version(source_dir: Path, explicit: str | None,
                            library_name: str) -> str:
    """The library's own version: nori.toml when present, else --lib-version (CE3505).

    A package IS one version, so a nori.toml beside the sources is the source of truth.
    An explicit flag that contradicts it is rejected rather than silently preferred --
    either way round, a package could otherwise ship under a version it does not claim.
    """
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.internals.semver import InvalidVersion, Version

    declared: str | None = None
    try:
        from sushi_lang.packager.manifest import ManifestError, load_manifest
        from sushi_lang.packager.paths import find_project_root

        root = find_project_root(source_dir)
        if root is not None:
            declared = load_manifest(root).version
    except (ManifestError, OSError, ValueError):
        # An unreadable or invalid nori.toml is the packager's problem to report, not a
        # reason to fail a --lib build that carries its own --lib-version.
        declared = None

    if declared is not None and explicit is not None and declared != explicit:
        raise LibraryError("CE3505", lib=library_name,
                           reason=f"nori.toml says {declared}, --lib-version says {explicit}")

    chosen = declared if declared is not None else explicit
    if chosen is None:
        raise LibraryError("CE3505", lib=library_name,
                           reason="no nori.toml beside the sources and no --lib-version")

    try:
        Version.parse(chosen)
    except InvalidVersion as e:
        raise LibraryError("CE3505", lib=library_name, reason=str(e)) from e
    return chosen


class LibraryManifestGenerator:
    """Generates .slib library files."""

    def __init__(self, analyzer: 'SemanticAnalyzer'):
        """Initialize manifest generator."""
        self.analyzer = analyzer
        self.structs = analyzer.structs
        self.enums = analyzer.enums

    def generate(self, units: list['Unit'], output_path: Path, bitcode: bytes,
                 templates: dict | None = None, library_version: str = "0.0.0",
                 kind: str = "binary", source: dict[str, str] | None = None) -> None:
        """Generate .slib library file."""
        from sushi_lang.backend.platform_detect import current_platform_name
        from sushi_lang.internals.version import _get_versions
        from sushi_lang.backend.library_format import LibraryFormat

        platform_name = current_platform_name()
        VERSION = _get_versions()["app"]

        library_name = output_path.stem

        # The public API is extracted FIRST, because that is what can reject the library
        # (CE0116, CE5002). A rejected build writes no container at all, and the pipeline
        # turns the reporter's state into the exit code (#436).
        public_functions = self._extract_public_functions(units)
        if self.analyzer.reporter.has_errors:
            return

        templates_section = (
            templates if templates is not None else self._extract_templates(units)
        )

        manifest = {
            "sushi_lib_version": "2.2",
            "library_name": library_name,
            "library_version": library_version,
            "kind": kind,
            "units": [unit.name for unit in own_units(units)],
            "requires_compiler": _requires_compiler(VERSION),
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform_name,
            "compiler_version": VERSION,
            "public_functions": public_functions,
            "public_constants": self._extract_public_constants(units),
            "structs": self._extract_structs(units),
            "enums": self._extract_enums(units),
            "templates": templates_section,
            "dependencies": self._extract_dependencies(units),
        }

        # What the library declares and does not export (#469). Absent when there is
        # nothing to keep, so a library of nothing but public functions grows by nothing.
        not_exported = self._extract_not_exported(units, templates_section)
        if not_exported:
            manifest["not_exported"] = not_exported

        # The types this library claims methods on and does not declare -- the
        # consumer's half of CW3003. Absent when the library extends only what
        # it declares, so most libraries grow by nothing.
        foreign = self._extract_foreign_extensions(units)
        if foreign:
            manifest["foreign_extensions"] = foreign

        # A map beside `units`, not a change to it: `units` is an ordered list and the
        # order is load-bearing for the consumer's injection. Absent when no unit
        # carries a block, so an undocumented library grows by nothing.
        unit_docs = self._extract_unit_docs(units)
        if unit_docs:
            manifest["unit_docs"] = unit_docs

        LibraryFormat.write(output_path, manifest, bitcode, source=source)

    def _contains_foreign_ptr(self, ty) -> bool:
        """Recursively check whether a type exposes a foreign `ptr` (ForeignPtrType)."""
        from sushi_lang.semantics.type_predicates import contains_foreign_ptr
        return contains_foreign_ptr(ty)

    def _extract_public_functions(self, units: list['Unit']) -> list[dict]:
        """Extract public function signatures from units."""
        import sushi_lang.internals.errors as er

        public_funcs = []

        for unit in units:
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

                # CE0116. A v1 native `...T` collects its trailing args into a runtime T[]
                # in ONE concrete function, so there is no template to monomorphize at the
                # consumer. A v2 pack `...Ts` carries type_params and left through the
                # template route above. The discriminator is is_variadic vs is_pack, NOT
                # the `...` spelling they share.
                # A rejection emits and moves on. `generate()` writes nothing once the
                # reporter holds an error, so the partial list this returns is discarded,
                # and every bad export is named in one build instead of one per build.
                # Raising here reached the top-level guard as a spurious CE0000 (#436).
                if any(getattr(p, "is_variadic", False) for p in func.params):
                    er.emit(self.analyzer.reporter, er.ERR.CE0116,
                            getattr(func, "name_span", None) or func.loc, name=func.name)
                    continue

                # CE5002: reject foreign `ptr` in a public library signature. The
                # typecheck pass's public-fn ptr fence (CE5008) tests the same condition
                # and exits earlier, so this is the backstop for a direct producer call.
                exposes_ptr = self._contains_foreign_ptr(func.ret) or any(
                    self._contains_foreign_ptr(p.ty) for p in func.params
                )
                if exposes_ptr:
                    er.emit(self.analyzer.reporter, er.ERR.CE5002,
                            getattr(func, "name_span", None) or func.loc, name=func.name)
                    continue

                public_funcs.append(with_doc({
                    "name": func.name,
                    "unit": unit.name,
                    "link_symbol": mangle_unit_symbol(unit.name, func.name),
                    **signature_record(func),
                }, func))

        return public_funcs

    def _extract_not_exported(self, units: list['Unit'], templates: dict) -> list[dict]:
        """Name what the library declares and keeps -- a name, and its kind (#469).

        The export closure ships the privates a public generic's body needs, and those
        carry a signature the consumer registers. A private no template names ships
        nowhere, so the consumer resolved it to nothing and heard CE2008 -- "undefined
        function" for a function this library defines. A name is enough to answer CE3005
        instead, so no signature, body or source travels here.

        Each private is named in exactly ONE place: the closure, or this list.
        """
        summary = templates.get("closure_summary") or {}
        shipped = (set(summary.get("private_functions", []))
                   | set(summary.get("private_generic_functions", []))
                   | set(summary.get("private_types", []))
                   | set(summary.get("constants", [])))

        kept: dict[str, str] = {}
        for unit in own_units(units):
            if unit.ast is None:
                continue
            for func in unit.ast.functions:
                if func.is_public or func.name in shipped:
                    continue
                # A monomorphized instance and a lifted lambda are both in this list by
                # now, and neither is a name a consumer can write.
                if getattr(func, "is_synthesized", False):
                    continue
                kept[func.name] = (
                    "generic_function" if func.type_params else "function"
                )
            # A type and a constant are kept the same way, and for the same reason: the
            # name reaches the consumer's tables not at all, so without it the answer was
            # CE2001 "unknown type" or CE1001 "undeclared identifier" about a declaration
            # this library does make.
            for struct_def in unit.ast.structs:
                if not struct_def.is_public and struct_def.name not in shipped:
                    kept[struct_def.name] = "struct"
            for enum_def in unit.ast.enums:
                if not enum_def.is_public and enum_def.name not in shipped:
                    kept[enum_def.name] = "enum"
            for const in unit.ast.constants:
                if not const.is_public and const.name not in shipped:
                    kept[const.name] = "constant"

        return [{"name": name, "kind": kept[name]} for name in sorted(kept)]

    def _extract_foreign_extensions(self, units: list['Unit']) -> list[dict]:
        """The foreign types this library claims methods on, in declaration order."""
        from sushi_lang.semantics.foreign_extensions import foreign_extension_claims

        return [{"type": claim.target, "method": claim.method,
                 "unit": claim.unit_name}
                for claim in foreign_extension_claims(own_units(units))]

    def _extract_public_constants(self, units: list['Unit']) -> list[dict]:
        """The constants this library MARKS public.

        Two gates, and both were missing. `own_units` is the filter the `units` index and
        the source section already use: a bundled stdlib module arrives as an ordinary
        unit at build time, so without it `<encoding/msgpack>`'s constants shipped as this
        library's API. And a constant carries a marker now, so an unmarked one is a
        decoder detail and not a promise.

        Each record carries the declaration's SOURCE as well as its type, because that is
        what a consumer needs to read the constant at all: a binary library ships bodies
        as bitcode, and a constant has no body to ship. It is the answer the export
        closure has always given for a private constant a template body names (#487).
        """
        from sushi_lang.semantics.library_templates import slice_decl_source

        public_consts = []

        for unit in own_units(units):
            if unit.ast is None:
                continue
            source = None
            for const in unit.ast.constants:
                if not const.is_public:
                    continue
                if source is None:
                    source = unit.file_path.read_text()
                public_consts.append(with_doc({
                    "name": const.name,
                    "unit": unit.name,
                    "type": self._type_to_string(const.ty),
                    "source": slice_decl_source(const, source),
                }, const))

        return public_consts

    def _extract_structs(self, units: list['Unit']) -> list[dict]:
        """The structs this library MARKS public.

        It had no gate, so a decoder detail shipped as frozen API and `--lib-info`
        printed its whole field layout.
        """
        structs = []
        seen_names = set()

        for unit in units:
            if unit.ast is None:
                continue
            for struct_def in unit.ast.structs:
                # Generic structs ship as re-parsable templates (see
                # _extract_templates), never as concrete entries.
                if struct_def.type_params:
                    continue
                if not struct_def.is_public:
                    continue
                if struct_def.name in seen_names:
                    continue
                seen_names.add(struct_def.name)

                structs.append(with_doc({
                    "name": struct_def.name,
                    "unit": unit.name,
                    "fields": [
                        with_doc({"name": field.name,
                                  "type": self._type_to_string(field.ty)}, field)
                        for field in struct_def.fields
                    ],
                    "is_generic": False,
                    "type_params": [],
                }, struct_def))

        return structs

    def _extract_enums(self, units: list['Unit']) -> list[dict]:
        """The enums this library MARKS public. It had no gate either."""
        enums = []
        seen_names = set()

        for unit in units:
            if unit.ast is None:
                continue
            for enum_def in unit.ast.enums:
                # Generic enums ship as re-parsable templates (see
                # _extract_templates), never as concrete entries.
                if enum_def.type_params:
                    continue
                if not enum_def.is_public:
                    continue
                if enum_def.name in seen_names:
                    continue
                seen_names.add(enum_def.name)

                variants = []
                for variant in enum_def.variants:
                    has_data = len(variant.associated_types) > 0
                    data_type = self._type_to_string(variant.associated_types[0]) if has_data else None
                    variants.append(with_doc({
                        "name": variant.name,
                        "has_data": has_data,
                        "data_type": data_type,
                    }, variant))

                enums.append(with_doc({
                    "name": enum_def.name,
                    "unit": unit.name,
                    "variants": variants,
                    "is_generic": False,
                    "type_params": [],
                }, enum_def))

        return enums

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

    def _scan_referenced_symbols(self, node, acc: set[str]) -> None:
        """Walk a body AST collecting referenced free symbol names."""
        from sushi_lang.semantics import ast as A

        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                self._scan_referenced_symbols(item, acc)
            return

        if isinstance(node, A.Name):
            acc.add(node.id)
        elif isinstance(node, A.Call):
            callee = node.callee
            if isinstance(callee, A.Name):
                acc.add(callee.id)
        elif isinstance(node, A.EnumConstructor):
            acc.add(node.enum_name)

        import dataclasses
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                self._scan_referenced_symbols(getattr(node, f.name, None), acc)

    def _scan_referenced_type_names(self, node, acc: set[str]) -> None:
        """Walk a declaration collecting referenced user-TYPE names."""
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef

        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                self._scan_referenced_type_names(item, acc)
            return

        if isinstance(node, UnknownType):
            acc.add(node.name)
        elif isinstance(node, GenericTypeRef):
            acc.add(node.base_name)

        import dataclasses
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                self._scan_referenced_type_names(getattr(node, f.name, None), acc)

    def _compute_export_closure(self, units: list['Unit'], exported: list) -> dict:
        """Walk every exported generic and collect the library-private symbols its body
        (transitively) depends on - the EXPORT CLOSURE (C4b/C5).

        Every index is keyed `(unit, name)`, and a reference resolves from the unit
        whose body names it: own unit first, then the flat view (#494). A name a
        template body resolves to a private concrete function is recorded in that
        template's BINDINGS map, name -> link symbol, because a binary library has no
        `Unit` at the consumer and no scope to resolve the re-parsed body against (D4).
        `external_namespaces` stays flat on purpose: a namespace bound in another unit
        over-rejects at worst, and CE5006 already refuses a template that names one.
        """
        import sushi_lang.internals.errors as er
        from sushi_lang.semantics.unit_symbols import mangle_unit_symbol

        priv_concrete_fns: dict[tuple[str, str], tuple] = {}
        priv_generic_fns: dict[tuple[str, str], tuple] = {}
        constants: dict[tuple[str, str], tuple] = {}
        types_by_name: dict[tuple[str, str], tuple] = {}
        external_namespaces: set[str] = set()

        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for fn in unit.ast.functions:
                if fn.type_params:
                    if not getattr(fn, "is_public", False):
                        priv_generic_fns[(unit.name, fn.name)] = (fn, source)
                elif not getattr(fn, "is_public", False):
                    priv_concrete_fns[(unit.name, fn.name)] = (fn, source)
            for c in unit.ast.constants:
                constants[(unit.name, c.name)] = (c, source)
            for s in unit.ast.structs:
                types_by_name[(unit.name, s.name)] = (s, source)
            for e in unit.ast.enums:
                types_by_name[(unit.name, e.name)] = (e, source)
            for ext in getattr(unit.ast, "externals", None) or []:
                external_namespaces.add(ext.namespace)

        def _resolve(index: dict, unit: str, name: str):
            """Own unit first, then the flat view -- first declaration wins, which is
            insertion order, which is the compilation order."""
            hit = index.get((unit, name))
            if hit is not None:
                return (unit, name), hit
            for (u, n), payload in index.items():
                if n == name:
                    return (u, n), payload
            return None, None

        shipped_fns: dict[tuple[str, str], tuple] = {}
        shipped_generic_fns: dict[tuple[str, str], tuple] = {}
        shipped_consts: dict[tuple[str, str], tuple] = {}
        shipped_types: dict[tuple[str, str], tuple] = {}
        bindings: dict[tuple[str, str], dict[str, str]] = {}
        visited: set[tuple[str, str]] = set()

        rejected = False

        def _reject(root, symbol: str) -> None:
            """Emit CE5006 and mark the closure rejected.

            This used to raise ValueError, which reached the top-level guard and printed
            a spurious CE0000 over the real diagnostic (#436). The raise also carried the
            control flow: it stopped the walk, and every caller below relies on that, so
            each one now returns explicitly once this has fired.
            """
            nonlocal rejected
            rejected = True
            er.emit(self.analyzer.reporter, er.ERR.CE5006,
                    getattr(root, "name_span", None) or root.loc,
                    name=root.name, symbol=symbol)

        def _walk(node, root, unit: str, sink: dict | None) -> None:
            """`unit` is whose body `node` is; `sink` is the bindings map of the
            template this body ships as SOURCE with, or None where the body ships
            as bitcode and its references resolve at the producer's link."""
            if rejected:
                return

            refs: set[str] = set()
            self._scan_referenced_symbols(node, refs)
            self._scan_referenced_type_names(node, refs)

            own = {getattr(node, "name", None)}
            own |= {tp.name for tp in (getattr(node, "type_params", None) or [])}
            own |= {p.name for p in (getattr(node, "params", None) or [])}

            for name in sorted(refs - own):
                if rejected:
                    return
                if name in external_namespaces:
                    _reject(root, name)
                    return
                key, payload = _resolve(priv_concrete_fns, unit, name)
                if payload is not None:
                    fn, src = payload
                    if sink is not None:
                        sink[name] = mangle_unit_symbol(key[0], name)
                    if key in visited:
                        continue
                    if any(getattr(p, "is_variadic", False) for p in fn.params):
                        _reject(root, name)
                        return
                    if self._contains_foreign_ptr(fn.ret) or any(
                        self._contains_foreign_ptr(p.ty) for p in fn.params
                    ):
                        _reject(root, name)
                        return
                    visited.add(key)
                    shipped_fns[key] = (fn, src)
                    _walk(fn, root, key[0], None)
                    continue
                key, payload = _resolve(priv_generic_fns, unit, name)
                if payload is not None:
                    if key in visited:
                        continue
                    fn, src = payload
                    visited.add(key)
                    shipped_generic_fns[key] = (fn, src)
                    _walk(fn, root, key[0], bindings.setdefault(key, {}))
                    continue
                key, payload = _resolve(constants, unit, name)
                if payload is not None:
                    if key in visited:
                        continue
                    c, src = payload
                    visited.add(key)
                    shipped_consts[key] = (c, src)
                    _walk(c, root, key[0], None)
                    continue
                key, payload = _resolve(types_by_name, unit, name)
                if payload is not None:
                    if key in visited:
                        continue
                    tnode, src = payload
                    visited.add(key)
                    # A private type has to travel with the template that names it. The
                    # public list is gated on the marker, so before this the template
                    # arrived at the consumer with a type nothing had registered, and the
                    # transplanted body was CE2001 "unknown type" about the library's own
                    # struct.
                    if not getattr(tnode, "is_public", True):
                        shipped_types[key] = (tnode, src)
                    _walk(tnode, root, key[0], None)

        for node, _source, unit_name in exported:
            _walk(node, node, unit_name,
                  bindings.setdefault((unit_name, node.name), {}))
            if rejected:
                break

        return {
            "private_functions": [(fn, src, u) for (u, _n), (fn, src) in shipped_fns.items()],
            "private_generic_functions": [(fn, src, u) for (u, _n), (fn, src) in shipped_generic_fns.items()],
            "constants": [(c, src, u) for (u, _n), (c, src) in shipped_consts.items()],
            "private_types": [(t, src, u) for (u, _n), (t, src) in shipped_types.items()],
            "bindings": bindings,
        }

    def _extract_templates(self, units: list['Unit']) -> dict:
        """Extract instantiable public generic templates (re-parsable source)."""
        from sushi_lang.semantics.library_templates import (
            serialize_generic_function, serialize_generic_struct,
            serialize_generic_enum, serialize_perk, serialize_perk_impl,
            serialize_generic_perk_impl, slice_decl_source,
        )
        from sushi_lang.compiler.pipeline import TEMPLATES_SCHEMA_VERSION

        generic_functions: list[dict] = []
        generic_structs: list[dict] = []
        generic_enums: list[dict] = []
        referenced_perks: set[str] = set()
        exported: list[tuple] = []

        # Every record names the unit that declared it. A template has no `link_symbol`
        # -- it is monomorphized at the consumer, so its instances take the consumer's
        # mangling -- but the unit is what an alias binds to, and for a BINARY library
        # the manifest is the only place that can say (section 3.1).
        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for func in unit.ast.functions:
                if not (func.is_public and func.type_params):
                    continue
                exported.append((func, source, unit.name))
                record = serialize_generic_function(func, source)
                record["unit"] = unit.name
                generic_functions.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for struct in unit.ast.structs:
                if not struct.type_params:
                    continue
                exported.append((struct, source, unit.name))
                record = serialize_generic_struct(struct, source)
                record["unit"] = unit.name
                generic_structs.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for enum in unit.ast.enums:
                if not enum.type_params:
                    continue
                exported.append((enum, source, unit.name))
                record = serialize_generic_enum(enum, source)
                record["unit"] = unit.name
                generic_enums.append(record)
                referenced_perks.update(record.get("free_perks", []))

        # Walk the export closure: collect transitive private dependencies
        # (shipping them below) and reject un-shippable references (CE5006).
        closure = self._compute_export_closure(units, exported)

        # D4: each template that ships as SOURCE carries the map from every free
        # name its body resolved to the symbol the producer resolved it to. Absent
        # when the body resolved nothing, so a self-contained template grows by
        # nothing.
        for record in generic_functions:
            resolved = closure["bindings"].get((record["unit"], record["name"]))
            if resolved:
                record["bindings"] = resolved

        for fn, src, unit_name in closure["private_generic_functions"]:
            record = serialize_generic_function(fn, src)
            record["unit"] = unit_name
            # A private symbol is not part of the documented API, so the record that
            # marks one drops its doc on the same line (documentation.md S8, R4).
            record["private"] = True
            record.pop("doc", None)
            resolved = closure["bindings"].get((unit_name, fn.name))
            if resolved:
                record["bindings"] = resolved
            generic_functions.append(record)
            referenced_perks.update(record.get("free_perks", []))

        # The same builder the public records use: a private helper is called from a
        # monomorphized template body, so the consumer needs the same answer to "who
        # frees this argument?".
        private_functions = [
            {
                "name": fn.name,
                "unit": unit_name,
                "link_symbol": mangle_unit_symbol(unit_name, fn.name),
                **signature_record(fn),
            }
            for fn, _src, unit_name in closure["private_functions"]
        ]
        shipped_constants = [
            {"name": c.name, "unit": unit_name, "source": slice_decl_source(c, src)}
            for c, src, unit_name in closure["constants"]
        ]
        # A private type travels as source, exactly as a private constant does, and for
        # the same reason: the consumer re-parses it and registers it so a transplanted
        # template body can name it. It is NOT in the public `structs` / `enums` index,
        # which is what the marker gates.
        shipped_types = [
            {"name": node.name, "unit": unit_name, "source": slice_decl_source(node, src)}
            for node, src, unit_name in closure["private_types"]
        ]
        closure_summary = {
            "private_functions": sorted({r["name"] for r in private_functions}),
            "private_generic_functions": sorted(
                {fn.name for fn, _, _ in closure["private_generic_functions"]}
            ),
            "constants": sorted({r["name"] for r in shipped_constants}),
            "private_types": sorted({r["name"] for r in shipped_types}),
        }

        # A generic-target perk implementation is a TEMPLATE (#543). The collect pass
        # filed it in `generic_perk_impls`; what `perk_impls` below holds for it are the
        # monomorphized copies, which the consumer cuts for itself from this source.
        generic_perk_impls: list[dict] = []
        template_keys: set[tuple[str, str]] = set()
        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for impl in getattr(unit.ast, "generic_perk_impls", None) or []:
                template_keys.add((impl.target_type.base_name, impl.perk_name))
                if any(
                    self._contains_foreign_ptr(m.ret)
                    or any(self._contains_foreign_ptr(p.ty) for p in m.params)
                    for m in impl.methods
                ):
                    continue
                record = serialize_generic_perk_impl(impl, source)
                record["unit"] = unit.name
                generic_perk_impls.append(record)
                referenced_perks.add(impl.perk_name)

        # Every PUBLIC perk ships: it is part of the API, whether or not a generic
        # constraint names it (#543 -- a contract a consumer could not name was a
        # contract whose implementation could not be registered). A perk a template
        # names ships too, as before.
        perks: list[dict] = []
        seen_perks: set[str] = set()
        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for perk in unit.ast.perks:
                if perk.name in seen_perks:
                    continue
                if not (perk.is_public or perk.name in referenced_perks):
                    continue
                seen_perks.add(perk.name)
                record = serialize_perk(perk, source)
                record["unit"] = unit.name
                perks.append(record)

        from sushi_lang.semantics.passes.collect.perks import _get_type_name
        from sushi_lang.semantics.generics.types import GenericTypeRef

        perk_impls: list[dict] = []
        seen_impls: set[tuple[str, str]] = set()
        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for impl in unit.ast.perk_impls:
                if impl.perk_name not in seen_perks:
                    continue
                if isinstance(impl.target_type, GenericTypeRef):
                    continue
                type_name = _get_type_name(impl.target_type)
                if type_name is None or (type_name, impl.perk_name) in seen_impls:
                    continue
                # A monomorphized copy of a template is an ordinary implementation by
                # design, and its source slice is the TEMPLATE's: shipping it as a
                # concrete record re-parsed to `Box@(T)` at the consumer (#543). The
                # template ships instead, and the consumer cuts its own copies.
                if (type_name.split("<", 1)[0], impl.perk_name) in template_keys:
                    continue
                if any(
                    self._contains_foreign_ptr(m.ret)
                    or any(self._contains_foreign_ptr(p.ty) for p in m.params)
                    for m in impl.methods
                ):
                    continue
                seen_impls.add((type_name, impl.perk_name))
                record = serialize_perk_impl(impl, source)
                record["unit"] = unit.name
                perk_impls.append(record)

        return {
            # 5: every record carries its unit, and a source-shipped template carries
            # `bindings` (D4). An older consumer resolves the flat way, so an old
            # compiler is refused by the container's requires_compiler, and an old
            # LIBRARY is refused by the consumer's templates gate (decision B).
            # 6: every public perk ships, and a generic-target perk implementation
            # ships as a template (#543). A version-5 library carries neither, so a
            # consumer would answer CE2008 for a method the library implements.
            "version": TEMPLATES_SCHEMA_VERSION,
            "generic_functions": generic_functions,
            "generic_structs": generic_structs,
            "generic_enums": generic_enums,
            "perks": perks,
            "perk_impls": perk_impls,
            "generic_perk_impls": generic_perk_impls,
            "private_functions": private_functions,
            "constants": shipped_constants,
            "private_types": shipped_types,
            "closure_summary": closure_summary,
        }

    def _extract_dependencies(self, units: list['Unit']) -> list[str]:
        """Extract stdlib dependencies from all units."""
        deps = set()

        for unit in units:
            if unit.ast is None:
                continue
            for use_stmt in unit.ast.uses:
                if use_stmt.is_stdlib:
                    deps.add(use_stmt.path)

        return sorted(deps)

    def _type_to_string(self, ty) -> str:
        """Convert Type object to string representation."""
        return type_string(ty)
