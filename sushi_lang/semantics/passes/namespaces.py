"""The `namespaces` pass: bind one unit's namespaces, and refuse a misplaced import.

`docs/design/unit-namespaces.md` section 3.2 fixes the position. A provider needs what
`collect` and `libraries` produce and nothing later, and `ffi-clash` is the first step
that asks whether a name is already taken -- so the pass stands between the two.

It answers three rules per unit: every import comes first (`CE3014`), an alias binds a
name nothing else in the unit binds (`CE3013`), and an `as` that reaches no name says so
(`CW3004`).

Every `use` statement is read here, aliased or not, because one statement contributes to
exactly one half of the same answer: `as` puts what the import brings behind a dot, and
its absence puts it into the unit's flat scope (section 6). One walk, one provider per
statement, and the two halves cannot disagree about what an import brought.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import Program, UseStatement
from sushi_lang.semantics.namespaces import (
    GENERIC_UNIT_TYPES,
    ExternalNamespace,
    GenericNamespace,
    NamespaceTable,
    Provider,
    StdlibNamespace,
    UnitNamespace,
    UnitScope,
)

# The kinds a namespace holds that a qualified form cannot reach yet. They are members
# all the same, so an alias over a unit of nothing but types is not an empty namespace.
_MEMBER_ONLY_KINDS = frozenset({"struct", "enum", "perk"})


def build_namespaces(reporter: Reporter, unit: Any, tables: Any, *,
                     units: Dict[str, Any],
                     library_registry: Any = None) -> NamespaceTable:
    """Bind every namespace `unit` may write, and report what its imports get wrong."""
    program: Program = unit.ast

    _reject_use_below_declaration(reporter, unit)

    table = NamespaceTable()

    # The FFI namespaces THIS unit declares. An `unsafe external` block is declared in
    # one unit (section 3), so the namespace it binds is that unit's, exactly as a `use`
    # is (#503). The table was program-wide until the per-unit scope existed to say what
    # the reach should be instead.
    external_table = getattr(tables, "externals", None)
    for block in program.externals or ():
        table.bind(block.namespace, ExternalNamespace(external_table, block.namespace))

    declared = _names_declared_by(program)
    flat: list[Tuple[UseStatement, Provider]] = []

    for use_stmt in program.uses or ():
        provider = _provider_for(use_stmt, tables, units, library_registry, unit)
        alias = use_stmt.alias
        if alias is None:
            flat.append((use_stmt, provider))
            continue
        if _reject_alias_collision(reporter, table, declared, use_stmt, alias):
            continue
        table.bind(alias, provider, use_stmt.alias_span or use_stmt.loc)
        if not tuple(provider.members()):
            er.emit(reporter, er.ERR.CW3004,
                    use_stmt.alias_span or use_stmt.loc, alias=alias)

    table.scope = _scope_of(unit, flat, units)
    return table


def _scope_of(unit: Any, flat: Iterable[Tuple[UseStatement, Provider]],
              units: Dict[str, Any]) -> UnitScope:
    """What this unit may write with no qualifier, from its FLAT imports alone.

    A LIBRARY import names the whole artifact: a `.slib` has no syntax for naming one
    of its units, so scoping the import to the matched unit would leave a multi-unit
    library's second unit unreachable with no escape. A library unit importing its own
    sibling wrote an ordinary `use`, and gets the sibling and nothing more.
    """
    scoped_units: list[str] = []
    modules: list[str] = []
    generics: list[str] = []
    for use_stmt, provider in flat:
        if provider.namespace_kind == "stdlib":
            modules.append(provider.origin)
        elif provider.namespace_kind == "generic":
            generics.extend(provider.members())
        elif provider.namespace_kind == "unit":
            scoped_units.append(provider.origin)
            if use_stmt.is_library:
                scoped_units.extend(_library_units(provider.origin, units))
    return UnitScope(unit=unit.name, units=tuple(dict.fromkeys(scoped_units)),
                     modules=tuple(dict.fromkeys(modules)),
                     generics=tuple(dict.fromkeys(generics)), everything=False)


def _library_units(unit_name: str, units: Dict[str, Any]) -> Iterable[str]:
    """Every unit of the source library `unit_name` belongs to, if it is one."""
    if not unit_name.startswith("lib/"):
        return ()
    library = "/".join(unit_name.split("/")[:2])
    return tuple(name for name in units if name.startswith(f"{library}/"))


def _reject_use_below_declaration(reporter: Reporter, unit: Any) -> None:
    """CE3014: every import stands above the first declaration (section 2.1).

    The span comes from the AST BUILDER, which is the only place source order survives:
    a library's constants and private types are appended to a host unit's lists by the
    `libraries` step above, and each carries a span from its own file.
    """
    # A unit the consumer did not write is skipped for the reason the `docs` pass skips
    # one: only the author can move the line, and only the author's own build is where
    # the rule can be acted on.
    if unit.provenance is not None:
        return
    first = unit.ast.first_declaration_span
    if first is None:
        return
    for use_stmt in unit.ast.uses or ():
        if use_stmt.loc is not None and use_stmt.loc.line > first.line:
            er.emit_with(reporter, er.ERR.CE3014, use_stmt.loc) \
                .note("this declaration comes first", first).emit()


def _top_level_declarations(program: Program) -> Iterable[Any]:
    """Every node a `use` must stand above."""
    for group in ("constants", "structs", "enums", "perks", "functions", "extensions",
                  "generic_extensions", "perk_impls", "externals"):
        yield from (getattr(program, group, None) or ())


def _names_declared_by(program: Program) -> Dict[str, Span]:
    """The names this unit declares itself, each with the span that declares it."""
    names: Dict[str, Span] = {}
    for node in _top_level_declarations(program):
        name = getattr(node, "name", None)
        if isinstance(name, str) and name not in names:
            names[name] = getattr(node, "name_span", None) or getattr(node, "loc", None)
    for block in program.externals or ():
        names.setdefault(block.namespace, block.loc)
    return names


def _reject_alias_collision(reporter: Reporter, table: NamespaceTable,
                            declared: Dict[str, Span], use_stmt: UseStatement,
                            alias: str) -> bool:
    """CE3013: one name holds one namespace. True when the alias was refused."""
    span = use_stmt.alias_span or use_stmt.loc
    if alias == "_":
        er.emit_with(reporter, er.ERR.CE3013, span, alias=alias) \
            .help("`_` is the discard name; it cannot name a namespace") \
            .emit()
        return True
    if table.is_namespace(alias):
        diagnostic = er.emit_with(reporter, er.ERR.CE3013, span, alias=alias)
        bound_at = table.bound_at(alias)
        if bound_at is not None:
            diagnostic = diagnostic.note("first bound here", bound_at)
        else:
            diagnostic = diagnostic.help(
                "an `unsafe external` block already binds this namespace")
        diagnostic.emit()
        return True
    if alias in declared:
        er.emit_with(reporter, er.ERR.CE3013, span, alias=alias) \
            .note("this unit declares the name here", declared[alias]) \
            .emit()
        return True
    return False


def _provider_for(use_stmt: UseStatement, tables: Any, units: Dict[str, Any],
                  library_registry: Any, host: Any = None) -> Provider:
    """What one `use` brings. A provider, never a written path (section 3.1)."""
    if use_stmt.is_library:
        return _library_provider(use_stmt, tables, units, library_registry)
    if use_stmt.is_stdlib:
        return _stdlib_provider(use_stmt, tables, units)
    return _unit_provider(_imported_unit(use_stmt.path, host, units), tables)


def _imported_unit(path: str, host: Any, units: Dict[str, Any]) -> str:
    """Which UNIT a `use "path"` names, from the unit that wrote it.

    A path is main-relative and is the unit name for every unit the consumer wrote.
    A source library's units were RENAMED when they were injected (`lib/<name>/<unit>`)
    and their `use` statements were not, so a library unit importing its sibling means
    the sibling under its own library's prefix.
    """
    if not getattr(host, "from_library", False):
        return path
    name = getattr(host, "name", "") or ""
    if not name.startswith("lib/"):
        return path
    candidate = f'{"/".join(name.split("/")[:2])}/{path}'
    return candidate if candidate in units else path


def _unit_provider(unit_name: str, tables: Any) -> UnitNamespace:
    """A compilation unit's own declarations, from the collect pass's tables."""
    others = {
        name: kind
        for (kind, name), origin in getattr(tables.visibility, "by_key", {}).items()
        if origin.unit_name == unit_name and kind in _MEMBER_ONLY_KINDS
    }
    # A generic function has no per-unit view of its own (#495), so the declaring unit
    # is read from the visibility record beside it.
    generics = {
        name: definition
        for name, definition in tables.generic_funcs.by_name.items()
        if _declaring_unit(tables, "function", name) == unit_name
    }
    return UnitNamespace(
        unit_name,
        functions=dict(tables.funcs.by_unit.get(unit_name, {})),
        constants=dict(tables.constants.by_unit.get(unit_name, {})),
        generics=generics,
        others=others,
    )


def _declaring_unit(tables: Any, kind: str, name: str) -> Optional[str]:
    """Which unit declared `name`, as the visibility table recorded it."""
    origin = tables.visibility.origin(kind, name)
    return None if origin is None else origin.unit_name


def _stdlib_provider(use_stmt: UseStatement, tables: Any,
                     units: Dict[str, Any]) -> Provider:
    """One of the standard library's four shapes (section 4.3)."""
    from sushi_lang.semantics.stdlib_registry import (
        get_stdlib_registry, is_source_stdlib_module,
    )

    path = use_stmt.path
    if is_source_stdlib_module(path):
        return _unit_provider(path, tables)

    generic = GENERIC_UNIT_TYPES.get(path)
    if generic is not None:
        return GenericNamespace(path, {generic})

    module = get_stdlib_registry().get_module(path)
    if module is not None:
        return StdlibNamespace(path, module)

    # A method interface: the import enables methods on a type and brings no name.
    # CW3004 is what says so, at the `use` rather than at every call after it.
    return UnitNamespace(path, functions={}, constants={})


def _library_provider(use_stmt: UseStatement, tables: Any, units: Dict[str, Any],
                      library_registry: Any) -> Provider:
    """One unit of a library. The namespace is the unit, never the library (section 8)."""
    wanted = use_stmt.path.rsplit("/", 1)[-1]

    source_units = [name for name, unit in units.items()
                    if getattr(unit, "from_library", False)]
    matched = _one_of(source_units, lambda name: name.rsplit("/", 1)[-1] == wanted)
    if matched is not None:
        return _unit_provider(matched, tables)

    if library_registry is not None:
        provider = _binary_library_provider(wanted, tables, library_registry)
        if provider is not None:
            return provider

    return UnitNamespace(use_stmt.path, functions={}, constants={})


def _binary_library_provider(wanted: str, tables: Any,
                             library_registry: Any) -> Optional[UnitNamespace]:
    """A binary library has no AST: its records name their unit in the manifest."""
    for metadata in library_registry.get_all_libraries().values():
        manifest = metadata.raw_manifest or {}
        records = manifest.get("public_functions", []) or []
        unit_name = _manifest_unit(records, wanted)
        if unit_name is None:
            continue
        functions = {
            record["name"]: metadata.functions[record["name"]]
            for record in records
            if record.get("unit") == unit_name and record["name"] in metadata.functions
        }
        constants = {
            record["name"]: sig
            for record in manifest.get("public_constants", []) or []
            if record.get("unit") == unit_name
            and (sig := tables.constants.by_name.get(record["name"])) is not None
        }
        others = _manifest_types(manifest, unit_name)
        return UnitNamespace(f"{metadata.name}/{unit_name}", functions=functions,
                             constants=constants, others=others)
    return None


def _manifest_unit(records: Iterable[dict], wanted: str) -> Optional[str]:
    """Which of the library's units the import names: the match, or its only one."""
    names = {record.get("unit") for record in records if record.get("unit")}
    if wanted in names:
        return wanted
    return next(iter(names)) if len(names) == 1 else None


def _manifest_types(manifest: dict, unit_name: str) -> Dict[str, str]:
    """The struct and enum names one unit of a binary library publishes."""
    return {
        record["name"]: kind
        for key, kind in (("structs", "struct"), ("enums", "enum"))
        for record in (manifest.get(key, []) or [])
        if record.get("unit") == unit_name
    }


def _one_of(items: Iterable[str], predicate) -> Optional[str]:
    """The single item the predicate accepts, or None when it is not exactly one."""
    matches: Tuple[str, ...] = tuple(name for name in items if predicate(name))
    return matches[0] if len(matches) == 1 else None
