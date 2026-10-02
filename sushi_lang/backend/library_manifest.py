"""Library manifest generation for .slib files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterator

from sushi_lang.semantics.library_templates import (
    doc_record, signature_record, type_string, with_doc,
)
from sushi_lang.semantics.type_predicates import contains_foreign_ptr
from sushi_lang.semantics.unit_symbols import mangle_unit_symbol
from sushi_lang.semantics.ast import ExtendDef, Node, VarDef
from sushi_lang.semantics.generics.contracts import CONTRACTS
from sushi_lang.semantics.passes.collect.perks import PerkCollector

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer


# The predefined perks whose implementation overrides a derived method. A library
# ships its implementations of them although it declares none of them.
OVERRIDABLE_PREDEFINED_PERKS = frozenset({PerkCollector.HASHABLE_PERK, *CONTRACTS})

# Every perk the compiler predefines. An implementation of one is never an ordinary
# method of its type, so it never travels as an extension record.
PREDEFINED_PERKS = OVERRIDABLE_PREDEFINED_PERKS | {PerkCollector.DROP_PERK}


def own_units(units: list['Unit']) -> list['Unit']:
    """The library's OWN units -- the compilation order minus what the author did not write.

    A `use <collections/iter>` injects the bundled module as an ordinary unit, and a
    `use <lib/other>` over a source library injects its units the same way, so both
    reach the manifest generator alongside the library's own files. A consumer states
    each library and each module it uses for itself (`docs/libraries.md`, limitation
    1), so shipping either one's declarations puts a SECOND definition of every name
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


# The plain ast records a node field can hold, and the attributes that carry their types.
# The node walk treats a record as a leaf (`ast_walk.field_kind`), so the type scan
# opens it here.
_RECORD_TYPE_SLOTS = {
    "Param": ("ty",),
    "StructField": ("ty",),
    "EnumVariant": ("associated_types",),
    "PerkMethodSignature": ("params", "ret", "err_type"),
}


def _written_types(value) -> Iterator:
    """Every type one node field value holds: directly, in a list, or in a record."""
    if isinstance(value, Node):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _written_types(item)
        return
    slots = _RECORD_TYPE_SLOTS.get(type(value).__name__)
    if slots is None:
        yield value
        return
    for slot in slots:
        yield from _written_types(getattr(value, slot))


def _extension_target_name(target_type) -> str | None:
    """The declared type name an extension target names: the element of an array."""
    element = getattr(target_type, "base_type", None)
    return getattr(element if element is not None else target_type, "name", None)


def _binding_key(node, unit: str) -> tuple[str, str]:
    """The key of one exported template's bindings map: its unit and its name.

    An extension template is keyed by its target as well, because a unit may declare
    a function `tag` beside an extension method `tag`, and each has its own body.
    """
    if isinstance(node, ExtendDef):
        return unit, f"{type_string(node.target_type)}.{node.name}"
    return unit, node.name


class _ScopedIndex:
    """A `(unit, name)` index: the own unit first, then the name's first declaration.

    First declaration is insertion order, which is the compilation order (#494).
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], tuple] = {}
        self._first: dict[str, tuple[str, str]] = {}

    def add(self, unit: str, name: str, payload: tuple) -> None:
        self._by_key[(unit, name)] = payload
        self._first.setdefault(name, (unit, name))

    def resolve(self, unit: str, name: str):
        key: tuple[str, str] | None = (unit, name)
        if key not in self._by_key:
            key = self._first.get(name)
        if key is None:
            return None, None
        return key, self._by_key[key]


@dataclass
class _ClosureKind:
    """One index of the export closure, and what a reference into it does."""
    index: _ScopedIndex
    binds: bool = False
    refuses: Callable[[Any], bool] = lambda _node: False
    ships: Callable[[Any], bool] = lambda _node: True
    child_sink: Callable[[tuple[str, str]], dict | None] = lambda _key: None
    shipped: dict[tuple[str, str], tuple] = field(default_factory=dict)


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
        """Every own unit's complete source text, keyed by unit name.

        Whole files, not the per-declaration slices the binary path ships: a source
        library has no export closure to compute, because there is nothing to leave out.
        """
        return {u.name: self._source(u) for u in own_units(units)}

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
            "sushi_lib_version": "2.3",
            "library_name": library_name,
            "library_version": library_version,
            "kind": kind,
            "units": [unit.name for unit in own_units(units)],
            "requires_compiler": _requires_compiler(VERSION),
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform_name,
            "compiler_version": VERSION,
            "public_functions": public_functions,
            "public_constants": self._extract_public_bindings(units, variables=False),
            "public_variables": self._extract_public_bindings(units, variables=True),
            "structs": self._extract_public_types(units, "structs"),
            "enums": self._extract_public_types(units, "enums"),
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

        # What each unit hands on (#585). Absent when no unit says `public use`, so
        # an ordinary library grows by nothing. A source library carries the statement
        # in its text as well; the index answers without a parser either way.
        reexports = self._extract_reexports(units)
        if reexports:
            manifest["reexports"] = reexports

        # A map beside `units`, not a change to it: `units` is an ordered list and the
        # order is load-bearing for the consumer's injection. Absent when no unit
        # carries a block, so an undocumented library grows by nothing.
        unit_docs = self._extract_unit_docs(units)
        if unit_docs:
            manifest["unit_docs"] = unit_docs

        LibraryFormat.write(output_path, manifest, bitcode, source=source)

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
                    "link_symbol": mangle_unit_symbol(unit.name, func.name),
                    **signature_record(func),
                }, func))

        return public_funcs

    def _extract_not_exported(self, units: list['Unit'], templates: dict) -> list[dict]:
        """Name what the library declares and keeps -- a name, its kind and its unit (#469).

        The export closure ships the privates a public generic's body needs, and those
        carry a signature the consumer registers. A private no template names ships
        nowhere, so the consumer resolved it to nothing and heard CE2008 -- "undefined
        function" for a function this library defines. A name is enough to answer CE3005
        instead, so no signature, body or source travels here.

        Each private is named in exactly ONE place: the closure, or this list.

        A record is keyed by (unit, name), because two units may each keep a `helper`
        (#1112). The unit tells CE5013 which symbol the bitcode defines.
        """
        summary = templates.get("closure_summary") or {}
        shipped = (set(summary.get("private_functions", []))
                   | set(summary.get("private_generic_functions", []))
                   | set(summary.get("private_types", []))
                   | set(summary.get("constants", [])))

        kept: dict[tuple[str, str], str] = {}
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
                kept[(unit.name, func.name)] = (
                    "generic_function" if func.type_params else "function"
                )
            # A type and a constant are kept the same way, and for the same reason: the
            # name reaches the consumer's tables not at all, so without it the answer was
            # CE2001 "unknown type" or CE1001 "undeclared identifier" about a declaration
            # this library does make.
            for struct_def in unit.ast.structs:
                if not struct_def.is_public and struct_def.name not in shipped:
                    kept[(unit.name, struct_def.name)] = "struct"
            for enum_def in unit.ast.enums:
                if not enum_def.is_public and enum_def.name not in shipped:
                    kept[(unit.name, enum_def.name)] = "enum"
            for const in unit.ast.constants:
                if not const.is_public and const.name not in shipped:
                    kept[(unit.name, const.name)] = (
                        "variable" if isinstance(const, VarDef) else "constant")

        return [{"name": name, "kind": kept[(unit, name)], "unit": unit}
                for unit, name in sorted(kept, key=lambda key: (key[1], key[0]))]

    def _extract_foreign_extensions(self, units: list['Unit']) -> list[dict]:
        """The foreign types this library claims methods on, in declaration order."""
        from sushi_lang.semantics.foreign_extensions import foreign_extension_claims

        return [{"type": claim.target, "method": claim.method,
                 "unit": claim.unit_name}
                for claim in foreign_extension_claims(own_units(units))]

    def _extract_public_bindings(self, units: list['Unit'], variables: bool) -> list[dict]:
        """The constants, or the unit variables, this library MARKS public.

        `own_units` keeps a bundled stdlib module's declarations out, and an unmarked
        declaration is a decoder detail and not a promise. Each record carries the
        declaration's SOURCE as well as its type: a binary library ships bodies as
        bitcode, and a constant has no body to ship (#487). A variable's record adds the
        `link_symbol` of its ONE storage, which the consumer declares as external rather
        than re-evaluating (docs/design/unit-storage.md).
        """
        from sushi_lang.semantics.library_templates import slice_decl_source

        records = []
        for unit in own_units(units):
            if unit.ast is None:
                continue
            for decl in unit.ast.constants:
                if not decl.is_public or isinstance(decl, VarDef) != variables:
                    continue
                record = {
                    "name": decl.name,
                    "unit": unit.name,
                    "type": type_string(decl.ty),
                    "source": slice_decl_source(decl, self._source(unit)),
                }
                if variables:
                    record["link_symbol"] = mangle_unit_symbol(unit.name, decl.name)
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
        return {"variants": variants}

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
        from sushi_lang.semantics.ast_walk import walk_nodes

        def collect(current: A.Node) -> bool:
            if isinstance(current, A.Name):
                acc.add(current.id)
            elif isinstance(current, A.Call):
                if isinstance(current.callee, A.Name):
                    acc.add(current.callee.id)
            elif isinstance(current, A.EnumConstructor):
                acc.add(current.enum_name)
            return True

        walk_nodes(node, collect)

    def _scan_referenced_type_names(self, node, acc: set[str]) -> None:
        """Walk a declaration collecting referenced user-TYPE names."""
        from sushi_lang.semantics import ast as A
        from sushi_lang.semantics.ast_walk import node_fields, walk_nodes
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.type_walk import walk_named_types
        from sushi_lang.semantics.typesys import UnknownType

        def collect(current: A.Node) -> bool:
            for _name, value in node_fields(current):
                for written in _written_types(value):
                    for ty in walk_named_types(written, struct_type_args=True):
                        if isinstance(ty, UnknownType):
                            acc.add(ty.name)
                        elif isinstance(ty, GenericTypeRef):
                            acc.add(ty.base_name)
            return True

        walk_nodes(node, collect)

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

        priv_concrete_fns = _ScopedIndex()
        priv_generic_fns = _ScopedIndex()
        constants = _ScopedIndex()
        types_by_name = _ScopedIndex()
        external_namespaces: set[str] = set()

        for unit in own_units(units):
            if unit.ast is None:
                continue
            source = self._source(unit)
            for fn in unit.ast.functions:
                if getattr(fn, "is_public", False):
                    continue
                index = priv_generic_fns if fn.type_params else priv_concrete_fns
                index.add(unit.name, fn.name, (fn, source))
            for c in unit.ast.constants:
                constants.add(unit.name, c.name, (c, source))
            for s in unit.ast.structs:
                types_by_name.add(unit.name, s.name, (s, source))
            for e in unit.ast.enums:
                types_by_name.add(unit.name, e.name, (e, source))
            for ext in getattr(unit.ast, "externals", None) or []:
                external_namespaces.add(ext.namespace)

        bindings: dict[tuple[str, str], dict[str, str]] = {}
        visited: set[tuple[str, str]] = set()

        # One row per index, in the order a reference is resolved. A concrete function
        # ships as bitcode, so the template records its symbol and its own body resolves
        # at the producer's link; a generic one ships as source with a bindings map of
        # its own. A type ships only when it is private: the public index carries the rest.
        kinds = (
            _ClosureKind(priv_concrete_fns, binds=True, refuses=self._unshippable_fn),
            _ClosureKind(priv_generic_fns,
                         child_sink=lambda key: bindings.setdefault(key, {})),
            _ClosureKind(constants),
            _ClosureKind(types_by_name,
                         ships=lambda node: not getattr(node, "is_public", True)),
        )

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
                for kind in kinds:
                    key, payload = kind.index.resolve(unit, name)
                    if key is None:
                        continue
                    if kind.binds and sink is not None:
                        sink[name] = mangle_unit_symbol(key[0], name)
                    if key in visited:
                        break
                    target = payload[0]
                    if kind.refuses(target):
                        _reject(root, name)
                        return
                    visited.add(key)
                    if kind.ships(target):
                        kind.shipped[key] = payload
                    _walk(target, root, key[0], kind.child_sink(key))
                    break

        for node, _source, unit_name in exported:
            _walk(node, node, unit_name,
                  bindings.setdefault(_binding_key(node, unit_name), {}))
            if rejected:
                break

        def listed(kind: _ClosureKind) -> list[tuple]:
            return [(node, src, u) for (u, _n), (node, src) in kind.shipped.items()]

        return {
            "private_functions": listed(kinds[0]),
            "private_generic_functions": listed(kinds[1]),
            "constants": listed(kinds[2]),
            "private_types": listed(kinds[3]),
            "bindings": bindings,
        }

    def _extract_templates(self, units: list['Unit']) -> dict:
        """Extract instantiable public generic templates (re-parsable source)."""
        from sushi_lang.backend.library_format import TEMPLATES_SCHEMA_VERSION

        own = own_units(units)
        referenced_perks: set[str] = set()
        generic_functions, generic_structs, generic_enums, exported = (
            self._generic_templates(own, referenced_perks))
        generic_extensions = self._generic_extension_templates(own, exported)

        # Walk the export closure: collect transitive private dependencies
        # (shipping them below) and reject un-shippable references (CE5006).
        closure = self._compute_export_closure(units, exported)
        shipped = self._closure_records(closure, generic_functions, referenced_perks)
        for record, node in generic_extensions:
            resolved = closure["bindings"].get(_binding_key(node, record["unit"]))
            if resolved:
                record["bindings"] = resolved

        generic_perk_impls, template_keys = self._generic_perk_impl_templates(
            own, referenced_perks)
        perks, shipped_perks = self._shipped_perks(own, referenced_perks)
        # An implementation of a predefined contract is an override the consumer's
        # derived methods must read, and the consumer knows the contract already.
        perk_impls = self._concrete_perk_impls(
            own, shipped_perks | OVERRIDABLE_PREDEFINED_PERKS, template_keys)

        templates = {
            "version": TEMPLATES_SCHEMA_VERSION,
            "generic_functions": generic_functions,
            "generic_structs": generic_structs,
            "generic_enums": generic_enums,
            "perks": perks,
            "perk_impls": perk_impls,
            "generic_perk_impls": generic_perk_impls,
            **shipped,
        }
        # Absent when the library declares no extension method, so a library without
        # one grows by nothing.
        extensions = self._concrete_extensions(own, shipped, shipped_perks, template_keys)
        if extensions:
            templates["extensions"] = extensions
        if generic_extensions:
            templates["generic_extensions"] = [record for record, _ in generic_extensions]
        return templates

    def _generic_templates(self, own: list['Unit'], referenced_perks: set[str]):
        """The public generic functions and every generic struct and enum, as templates.

        Every record names the unit that declared it. A template has no `link_symbol`
        -- it is monomorphized at the consumer, so its instances take the consumer's
        mangling -- but the unit is what an alias binds to, and for a BINARY library
        the manifest is the only place that can say (section 3.1). `exported` keeps the
        declaration order, unit by unit, because the closure walk reads it in order.
        """
        from sushi_lang.semantics.library_templates import (
            serialize_generic_enum, serialize_generic_function, serialize_generic_struct,
        )

        functions: list[dict] = []
        structs: list[dict] = []
        enums: list[dict] = []
        exported: list[tuple] = []
        for unit in own:
            if unit.ast is None:
                continue
            source = self._source(unit)
            rows = (
                ([f for f in unit.ast.functions if f.is_public and f.type_params],
                 serialize_generic_function, functions),
                ([s for s in unit.ast.structs if s.type_params],
                 serialize_generic_struct, structs),
                ([e for e in unit.ast.enums if e.type_params],
                 serialize_generic_enum, enums),
            )
            for decls, serialize, records in rows:
                for decl in decls:
                    exported.append((decl, source, unit.name))
                    record = serialize(decl, source)
                    record["unit"] = unit.name
                    records.append(record)
                    referenced_perks.update(record.get("free_perks", []))
        return functions, structs, enums, exported

    def _generic_extension_templates(self, own: list['Unit'],
                                     exported: list) -> list[tuple[dict, ExtendDef]]:
        """Every extension TEMPLATE of the library's own units, with its declaration.

        Each one joins `exported`, so the export closure ships what its body names and
        binds each private function it calls (D4), as for a generic function.
        """
        from sushi_lang.semantics.library_templates import serialize_generic_extension

        records: list[tuple[dict, ExtendDef]] = []
        for unit in own:
            if unit.ast is None:
                continue
            source = self._source(unit)
            for ext in unit.ast.generic_extensions or []:
                exported.append((ext, source, unit.name))
                record = serialize_generic_extension(ext, source)
                record["unit"] = unit.name
                records.append((record, ext))
        return records

    def _concrete_extensions(self, own: list['Unit'], shipped: dict,
                             shipped_perks: set[str],
                             template_keys: set[tuple[str, str]]) -> list[dict]:
        """Every CONCRETE extension method a consumer can reach, as a signature record.

        The body is in the bitcode, so the record names the symbol the library defines.
        An extension on a private type that nothing ships is left out: no consumer can
        name the type, and no shipped body names it.

        The methods of an implementation of a perk that does not ship travel here too.
        The perk hides its CONTRACT, and the methods stay callable
        (`docs/design/visibility.md`); the consumer has no contract to register them
        under, so each one is an ordinary method of the type.
        """
        from sushi_lang.semantics.library_templates import (
            impl_method_symbol, method_record, serialize_extension)
        from sushi_lang.semantics.passes.collect.perks import _get_type_name
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.generics.name_mangling import (
            extension_receiver_name, extension_symbol)

        kept = self._kept_type_names(own, shipped)
        records: list[dict] = []
        for unit in own:
            if unit.ast is None:
                continue
            for ext in unit.ast.extensions:
                if _extension_target_name(ext.target_type) in kept:
                    continue
                record = serialize_extension(ext)
                record["unit"] = unit.name
                record["link_symbol"] = extension_symbol(
                    extension_receiver_name(ext.target_type), ext.name)
                records.append(record)
            for impl in unit.ast.perk_impls:
                if (impl.perk_name in shipped_perks
                        or impl.perk_name in PREDEFINED_PERKS
                        or isinstance(impl.target_type, GenericTypeRef)
                        or getattr(impl, "is_synthesized", False)):
                    continue
                type_name = _get_type_name(impl.target_type)
                if type_name is None or type_name in kept:
                    continue
                base_name = getattr(impl.target_type, "generic_base", None) or type_name
                if (base_name, impl.perk_name) in template_keys:
                    continue
                for method in impl.methods:
                    records.append({
                        **method_record(method),
                        "type": type_name,
                        "unit": unit.name,
                        "link_symbol": impl_method_symbol(type_name, method.name),
                    })
        return records

    def _kept_type_names(self, own: list['Unit'], shipped: dict) -> set[str]:
        """The private types of the library that no closure record ships."""
        shipped_types = {r["name"] for r in shipped.get("private_types", [])}
        kept: set[str] = set()
        for unit in own:
            if unit.ast is None:
                continue
            for decls in (unit.ast.structs, unit.ast.enums):
                kept.update(d.name for d in decls
                            if not d.is_public and d.name not in shipped_types)
        return kept

    def _closure_records(self, closure: dict, generic_functions: list[dict],
                         referenced_perks: set[str]) -> dict:
        """The records the export closure ships, and the summary that names them."""
        from sushi_lang.semantics.library_templates import (
            serialize_generic_function, slice_decl_source,
        )

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
        # A private unit variable a template body names travels as source too, and
        # carries the symbol of the ONE storage the consumer must declare, not define.
        shipped_constants = [
            {"name": c.name, "unit": unit_name, "source": slice_decl_source(c, src),
             **({"link_symbol": mangle_unit_symbol(unit_name, c.name)}
                if isinstance(c, VarDef) else {})}
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
        return {
            "private_functions": private_functions,
            "constants": shipped_constants,
            "private_types": shipped_types,
            "closure_summary": {
                "private_functions": sorted({r["name"] for r in private_functions}),
                "private_generic_functions": sorted(
                    {fn.name for fn, _, _ in closure["private_generic_functions"]}
                ),
                "constants": sorted({r["name"] for r in shipped_constants}),
                "private_types": sorted({r["name"] for r in shipped_types}),
            },
        }

    def _generic_perk_impl_templates(self, own: list['Unit'], referenced_perks: set[str]):
        """Every generic-target perk implementation, as a TEMPLATE (#543).

        The collect pass filed it in `generic_perk_impls`; what `perk_impls` holds for
        it are the monomorphized copies, which the consumer cuts for itself from this
        source. The keys name those copies, so the concrete index can leave them out.
        """
        from sushi_lang.semantics.library_templates import serialize_generic_perk_impl

        records: list[dict] = []
        template_keys: set[tuple[str, str]] = set()
        for unit in own:
            if unit.ast is None:
                continue
            for impl in getattr(unit.ast, "generic_perk_impls", None) or []:
                template_keys.add((impl.target_type.base_name, impl.perk_name))
                if self._impl_exposes_ptr(impl):
                    continue
                record = serialize_generic_perk_impl(impl, self._source(unit))
                record["unit"] = unit.name
                records.append(record)
                referenced_perks.add(impl.perk_name)
        return records, template_keys

    def _shipped_perks(self, own: list['Unit'],
                       referenced_perks: set[str]) -> tuple[list[dict], set[str]]:
        """Every PUBLIC perk, and every perk a template names.

        A public perk is part of the API whether or not a generic constraint names it
        (#543 -- a contract a consumer could not name was a contract whose
        implementation could not be registered).
        """
        from sushi_lang.semantics.library_templates import serialize_perk

        perks: list[dict] = []
        seen: set[str] = set()
        for unit in own:
            if unit.ast is None:
                continue
            for perk in unit.ast.perks:
                if perk.name in seen:
                    continue
                if not (perk.is_public or perk.name in referenced_perks):
                    continue
                seen.add(perk.name)
                record = serialize_perk(perk, self._source(unit))
                record["unit"] = unit.name
                perks.append(record)
        return perks, seen

    def _concrete_perk_impls(self, own: list['Unit'], shipped_perks: set[str],
                             template_keys: set[tuple[str, str]]) -> list[dict]:
        """Every concrete implementation of a shipped perk, once per (type, perk)."""
        from sushi_lang.semantics.library_templates import serialize_perk_impl
        from sushi_lang.semantics.passes.collect.perks import _get_type_name
        from sushi_lang.semantics.generics.types import GenericTypeRef

        records: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for unit in own:
            if unit.ast is None:
                continue
            for impl in unit.ast.perk_impls:
                if impl.perk_name not in shipped_perks:
                    continue
                if isinstance(impl.target_type, GenericTypeRef):
                    continue
                type_name = _get_type_name(impl.target_type)
                if type_name is None or (type_name, impl.perk_name) in seen:
                    continue
                # A monomorphized copy of a template is an ordinary implementation by
                # design, and its source slice is the TEMPLATE's: shipping it as a
                # concrete record re-parsed to `Box@(T)` at the consumer (#543). The
                # template ships instead, and the consumer cuts its own copies.
                base_name = getattr(impl.target_type, "generic_base", None) or type_name
                if (base_name, impl.perk_name) in template_keys:
                    continue
                if self._impl_exposes_ptr(impl):
                    continue
                seen.add((type_name, impl.perk_name))
                record = serialize_perk_impl(impl, self._source(unit))
                record["unit"] = unit.name
                records.append(record)
        return records

    def _impl_exposes_ptr(self, impl) -> bool:
        """Whether a perk implementation's methods expose a foreign `ptr`."""
        return any(
            contains_foreign_ptr(m.ret)
            or any(contains_foreign_ptr(p.ty) for p in m.params)
            for m in impl.methods
        )

    def _unshippable_fn(self, fn) -> bool:
        """A private concrete function the closure cannot ship: a native variadic has no
        template to monomorphize, and a foreign `ptr` may not cross the boundary."""
        return any(getattr(p, "is_variadic", False) for p in fn.params) or (
            contains_foreign_ptr(fn.ret)
            or any(contains_foreign_ptr(p.ty) for p in fn.params)
        )

    def _extract_reexports(self, units: list['Unit']) -> list[dict]:
        """What each own unit RE-EXPORTS: one record per `public use` (#585).

        `public use X` makes X's public names the unit's own, so the unit's importers
        get them where its own names land (`docs/design/unit-namespaces.md` section
        8.1). A SOURCE library needs no record -- its units arrive as text and the
        consumer's `namespaces` pass reads the statement like any other. A compiled
        library ships records, so the statement has to become one, or a consumer reads
        an API narrower than the author wrote. It was refused instead (CE3514, retired).

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
                        # happen: the path resolved to a compiled unit for the build
                        # to have got here. Recording nothing is still better than
                        # recording a name the consumer cannot look up.
                        continue
                    kind, path = "unit", sibling
                records.append({"unit": unit.name, "path": path, "kind": kind})
        return records

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
