"""Library manifest generation for .slib files."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sushi_lang.semantics.library_templates import doc_record, with_doc
from sushi_lang.semantics.param_modes import param_mode

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
            "sushi_lib_version": "2.0",
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
                    "params": [
                        # The MODE is its own field, not part of the type string. A
                        # `nom` cannot be spelled in a type at all, and reading peek /
                        # poke back out of a type string was the half that was missing
                        # (docs/design/borrow-model.md S10).
                        #
                        # A parameter record carries no `doc`: per-parameter text lives
                        # in the enclosing function's `doc.params`, keyed by name.
                        {"name": p.name, "type": self._type_to_string(p.ty),
                         "mode": param_mode(p).value}
                        for p in func.params
                    ],
                    "return_type": self._type_to_string(func.ret),
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
        shipped = set(summary.get("private_functions", [])) | set(
            summary.get("private_generic_functions", [])
        )

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

        return [{"name": name, "kind": kept[name]} for name in sorted(kept)]

    def _extract_public_constants(self, units: list['Unit']) -> list[dict]:
        """Extract public constants (all constants are public)."""
        public_consts = []

        for unit in units:
            if unit.ast is None:
                continue
            for const in unit.ast.constants:
                public_consts.append(with_doc({
                    "name": const.name,
                    "type": self._type_to_string(const.ty),
                }, const))

        return public_consts

    def _extract_structs(self, units: list['Unit']) -> list[dict]:
        """Extract struct definitions from units."""
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
                if struct_def.name in seen_names:
                    continue
                seen_names.add(struct_def.name)

                structs.append(with_doc({
                    "name": struct_def.name,
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
        """Extract enum definitions from units."""
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
        """
        import sushi_lang.internals.errors as er

        priv_concrete_fns: dict[str, tuple] = {}
        priv_generic_fns: dict[str, tuple] = {}
        constants: dict[str, tuple] = {}
        types_by_name: dict[str, tuple] = {}
        external_namespaces: set[str] = set()

        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for fn in unit.ast.functions:
                if fn.type_params:
                    if not getattr(fn, "is_public", False):
                        priv_generic_fns[fn.name] = (fn, source)
                elif not getattr(fn, "is_public", False):
                    priv_concrete_fns[fn.name] = (fn, source)
            for c in unit.ast.constants:
                constants[c.name] = (c, source)
            for s in unit.ast.structs:
                types_by_name[s.name] = (s, source)
            for e in unit.ast.enums:
                types_by_name[e.name] = (e, source)
            for ext in getattr(unit.ast, "externals", None) or []:
                external_namespaces.add(ext.namespace)

        shipped_fns: dict[str, tuple] = {}
        shipped_generic_fns: dict[str, tuple] = {}
        shipped_consts: dict[str, tuple] = {}
        visited: set[str] = set()

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

        def _walk(node, root) -> None:
            if rejected:
                return

            refs: set[str] = set()
            self._scan_referenced_symbols(node, refs)
            self._scan_referenced_type_names(node, refs)

            own = {getattr(node, "name", None)}
            own |= {tp.name for tp in (getattr(node, "type_params", None) or [])}
            own |= {p.name for p in (getattr(node, "params", None) or [])}

            for name in sorted(refs - own - visited):
                if rejected:
                    return
                if name in external_namespaces:
                    _reject(root, name)
                    return
                elif name in priv_concrete_fns:
                    fn, src = priv_concrete_fns[name]
                    if any(getattr(p, "is_variadic", False) for p in fn.params):
                        _reject(root, name)
                        return
                    if self._contains_foreign_ptr(fn.ret) or any(
                        self._contains_foreign_ptr(p.ty) for p in fn.params
                    ):
                        _reject(root, name)
                        return
                    visited.add(name)
                    shipped_fns[name] = (fn, src)
                    _walk(fn, root)
                elif name in priv_generic_fns:
                    fn, src = priv_generic_fns[name]
                    visited.add(name)
                    shipped_generic_fns[name] = (fn, src)
                    _walk(fn, root)
                elif name in constants:
                    c, src = constants[name]
                    visited.add(name)
                    shipped_consts[name] = (c, src)
                    _walk(c, root)
                elif name in types_by_name:
                    tnode, _src = types_by_name[name]
                    visited.add(name)
                    _walk(tnode, root)

        for node, _source in exported:
            _walk(node, node)
            if rejected:
                break

        return {
            "private_functions": list(shipped_fns.values()),
            "private_generic_functions": list(shipped_generic_fns.values()),
            "constants": list(shipped_consts.values()),
        }

    def _extract_templates(self, units: list['Unit']) -> dict:
        """Extract instantiable public generic templates (re-parsable source)."""
        from sushi_lang.semantics.library_templates import (
            serialize_generic_function, serialize_generic_struct,
            serialize_generic_enum, serialize_perk, serialize_perk_impl,
            slice_decl_source,
        )

        generic_functions: list[dict] = []
        generic_structs: list[dict] = []
        generic_enums: list[dict] = []
        referenced_perks: set[str] = set()
        exported: list[tuple] = []

        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for func in unit.ast.functions:
                if not (func.is_public and func.type_params):
                    continue
                exported.append((func, source))
                record = serialize_generic_function(func, source)
                generic_functions.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for struct in unit.ast.structs:
                if not struct.type_params:
                    continue
                exported.append((struct, source))
                record = serialize_generic_struct(struct, source)
                generic_structs.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for enum in unit.ast.enums:
                if not enum.type_params:
                    continue
                exported.append((enum, source))
                record = serialize_generic_enum(enum, source)
                generic_enums.append(record)
                referenced_perks.update(record.get("free_perks", []))

        # Walk the export closure: collect transitive private dependencies
        # (shipping them below) and reject un-shippable references (CE5006).
        closure = self._compute_export_closure(units, exported)

        for fn, src in closure["private_generic_functions"]:
            record = serialize_generic_function(fn, src)
            # A private symbol is not part of the documented API, so the record that
            # marks one drops its doc on the same line (documentation.md S8, R4).
            record["private"] = True
            record.pop("doc", None)
            generic_functions.append(record)
            referenced_perks.update(record.get("free_perks", []))

        private_functions = [
            {
                "name": fn.name,
                "params": [
                    # The mode travels with a private helper too: the consumer calls it
                    # from a monomorphized template body, so it needs the same answer to
                    # "who frees this argument?" the public records carry.
                    {"name": p.name, "type": self._type_to_string(p.ty),
                     "mode": param_mode(p).value}
                    for p in fn.params
                ],
                "return_type": self._type_to_string(fn.ret),
            }
            for fn, _src in closure["private_functions"]
        ]
        shipped_constants = [
            {"name": c.name, "source": slice_decl_source(c, src)}
            for c, src in closure["constants"]
        ]
        closure_summary = {
            "private_functions": sorted(r["name"] for r in private_functions),
            "private_generic_functions": sorted(
                fn.name for fn, _ in closure["private_generic_functions"]
            ),
            "constants": sorted(r["name"] for r in shipped_constants),
        }

        perks: list[dict] = []
        seen_perks: set[str] = set()
        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for perk in unit.ast.perks:
                if perk.name not in referenced_perks or perk.name in seen_perks:
                    continue
                seen_perks.add(perk.name)
                perks.append(serialize_perk(perk, source))

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
                if any(
                    self._contains_foreign_ptr(m.ret)
                    or any(self._contains_foreign_ptr(p.ty) for p in m.params)
                    for m in impl.methods
                ):
                    continue
                seen_impls.add((type_name, impl.perk_name))
                perk_impls.append(serialize_perk_impl(impl, source))

        return {
            "version": 4,
            "generic_functions": generic_functions,
            "generic_structs": generic_structs,
            "generic_enums": generic_enums,
            "perks": perks,
            "perk_impls": perk_impls,
            "private_functions": private_functions,
            "constants": shipped_constants,
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
        if ty is None:
            return "~"

        return str(ty)
