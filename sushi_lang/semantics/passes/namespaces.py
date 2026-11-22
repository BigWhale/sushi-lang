"""The `namespaces` pass: bind one unit's namespaces, and refuse a misplaced import.

`docs/design/unit-namespaces.md` section 3.2 fixes the position. A provider needs what
`collect` produces and nothing later, and `ffi-clash` is the first step
that asks whether a name is already taken -- so the pass stands between the two.

It answers five rules per unit: every import comes first (`CE3014`), an alias binds a
name nothing else in the unit binds (`CE3013`), an `as` that reaches no name says so
(`CW3004`), a `public use` takes no `as` (`CE3016`), and a `public use` that hands on
nothing says so (`CW3005`).

Every `use` statement is read here, aliased or not, because one statement contributes to
exactly one half of the same answer: `as` puts what the import brings behind a dot, and
its absence puts it into the unit's flat scope (section 6). One walk, one provider per
statement, and the two halves cannot disagree about what an import brought.

A provider carries what its unit RE-EXPORTS (section 8.1, Ruling 7): `_unit_provider`
reads the unit's own `public use` statements and builds a provider for each, recursively
and with a visited set, so a chain composes and a cycle terminates. The flat scope reads
the same chain through `Provider.reaches`, so the dot and the bare name agree.
"""
from __future__ import annotations

from typing import (
    TYPE_CHECKING, AbstractSet, Callable, Dict, Iterable, Optional, Tuple, cast,
)

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import Program, UseStatement
from sushi_lang.semantics.ast_walk import declarations
from sushi_lang.semantics.namespaces import (
    GENERIC_UNIT_TYPES,
    ExternalNamespace,
    GenericNamespace,
    NamespaceTable,
    Provider,
    StdlibNamespace,
    UnitNamespace,
    UnitScope,
    homed_enums,
)

if TYPE_CHECKING:
    # Type information only. Neither reaches this pass again, so no cycle is being
    # broken here: the guard keeps a pass the analyzer imports inside its own body from
    # pulling the table graph in at run time.
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.units import Unit

# The kinds a namespace holds that a qualified form cannot reach yet. They are members
# all the same, so an alias over a unit of nothing but types is not an empty namespace.
_MEMBER_ONLY_KINDS = frozenset({"struct", "enum", "perk"})

# What the declaration walk yields that lives INSIDE another declaration. A struct
# field, an enum variant, a perk method and an FFI declaration are each reached through
# the thing that holds them, so none of them contests an alias (CE3013).
_INNER_KINDS = frozenset({"field", "variant", "perk method", "external declaration",
                          "external variable"})


def build_namespaces(reporter: Reporter, unit: Unit, tables: SymbolTables, *,
                     units: Dict[str, Unit]) -> NamespaceTable:
    """Bind every namespace `unit` may write, and report what its imports get wrong."""
    # `Unit.ast` is optional, but the analyzer's loop skips a unit that has none, so
    # the pass never sees one. The cast says that and emits no code.
    program: Program = cast(Program, unit.ast)

    _reject_use_below_declaration(reporter, unit, program)

    table = NamespaceTable(library=unit.library_name)

    # The FFI namespaces THIS unit declares. An `unsafe external` block is declared in
    # one unit (section 3), so the namespace it binds is that unit's, exactly as a `use`
    # is (#503). The table was program-wide until the per-unit scope existed to say what
    # the reach should be instead.
    external_table = getattr(tables, "externals", None)
    for block in program.externals or ():
        table.bind(block.namespace,
                   ExternalNamespace(external_table, block.namespace, unit.name))

    declared = _names_declared_by(program)
    flat: list[Tuple[UseStatement, Provider]] = []
    aliased: list[Tuple[UseStatement, Provider]] = []

    for use_stmt in program.uses or ():
        provider = _provider_for(use_stmt, tables, units, unit)
        alias = use_stmt.alias
        if use_stmt.is_public and alias is not None:
            # A re-export is of names and not of a namespace (Ruling 7, rule 1). The
            # alias still binds below, so the one fault gets one diagnostic.
            er.emit(reporter, er.ERR.CE3016, use_stmt.alias_span or use_stmt.loc)
        elif use_stmt.is_public and not tuple(provider.public_members()):
            er.emit(reporter, er.ERR.CW3005,
                    use_stmt.public_span or use_stmt.loc, origin=provider.origin)
        if alias is None:
            flat.append((use_stmt, provider))
            continue
        if _reject_alias_collision(reporter, table, declared, use_stmt, alias):
            continue
        table.bind(alias, provider, use_stmt.alias_span or use_stmt.loc)
        aliased.append((use_stmt, provider))
        if not tuple(provider.members()):
            er.emit(reporter, er.ERR.CW3004,
                    use_stmt.alias_span or use_stmt.loc, alias=alias)

    table.scope = _scope_of(unit.name, flat, units, aliased)
    return table


class MethodInterfaceNamespace(UnitNamespace):
    """A stdlib import that brings no name, for example the directory import `<collections>`.

    It declares nothing, so its place in the scope decides no name. A method of a
    built-in type needs no import (`docs/design/extension-visibility.md` R1), so it does
    not decide a method either.
    """

    def __init__(self, module_path: str, homed: Optional[Dict[str, str]] = None) -> None:
        super().__init__(module_path, functions={}, constants={}, others=homed)


def _scope_of(unit_name: str, flat: Iterable[Tuple[UseStatement, Provider]],
              units: Dict[str, Unit],
              aliased: Iterable[Tuple[UseStatement, Provider]] = ()) -> UnitScope:
    """What this unit may write with no qualifier, from its FLAT imports alone.

    An import brings what it names AND what that re-exports (section 8.1): the walk is
    `Provider.reaches`, the same one the dot reads, so `use "shapes"` puts `geometry`'s
    unit into this scope when `shapes` says `public use "geometry"`.

    A LIBRARY import names the whole artifact: a `.slib` has no syntax for naming one
    of its units, so scoping the import to the matched unit would leave a multi-unit
    library's second unit unreachable with no escape. A library unit importing its own
    sibling wrote an ordinary `use`, and gets the sibling and nothing more.

    An ALIASED import puts no name here. The units it reaches are kept apart, because
    their public extensions are callable here (`docs/design/extension-visibility.md` R6)
    and their names are not.
    """
    scoped_units: list[str] = []
    modules: list[str] = []
    generics: list[str] = []
    for use_stmt, provider in flat:
        for reached in provider.reaches():
            if reached.namespace_kind == "stdlib":
                modules.append(reached.origin)
            elif reached.namespace_kind == "generic":
                generics.extend(reached.members())
        scoped_units.extend(_units_reached(use_stmt, provider, units))
    aliased_units: list[str] = []
    for use_stmt, provider in aliased:
        aliased_units.extend(_units_reached(use_stmt, provider, units))
    return UnitScope(unit=unit_name, units=tuple(dict.fromkeys(scoped_units)),
                     modules=tuple(dict.fromkeys(modules)),
                     generics=tuple(dict.fromkeys(generics)), everything=False,
                     aliased_units=tuple(dict.fromkeys(aliased_units)))


def _units_reached(use_stmt: UseStatement, provider: Provider,
                   units: Dict[str, Unit]) -> list[str]:
    """Every compilation unit one import reaches: the unit, and its `public use` chain.

    A LIBRARY import names the whole artifact, so it reaches every unit of the library.
    """
    found: list[str] = []
    for reached in provider.reaches():
        if reached.namespace_kind != "unit":
            continue
        found.append(reached.origin)
    if use_stmt.is_library and provider.namespace_kind == "unit":
        found.extend(_library_units(provider.origin, units))
    return found


def _library_units(unit_name: str, units: Dict[str, Unit]) -> Iterable[str]:
    """Every unit of the source library `unit_name` belongs to, if it is one."""
    if not unit_name.startswith("lib/"):
        return ()
    library = "/".join(unit_name.split("/")[:2])
    return tuple(name for name in units if name.startswith(f"{library}/"))


def _reject_use_below_declaration(reporter: Reporter, unit: Unit,
                                  program: Program) -> None:
    """CE3014: every import stands above the first declaration (section 2.1).

    The span comes from the AST BUILDER, which is the only place source order survives.
    """
    # A unit the consumer did not write is skipped for the reason the `docs` pass skips
    # one: only the author can move the line, and only the author's own build is where
    # the rule can be acted on.
    if unit.provenance is not None:
        return
    first = program.first_declaration_span
    if first is None:
        return
    for use_stmt in program.uses or ():
        if use_stmt.loc is not None and use_stmt.loc.line > first.line:
            er.emit_with(reporter, er.ERR.CE3014, use_stmt.loc) \
                .note_at("this declaration comes first", first).emit()


def _names_declared_by(program: Program) -> Dict[str, Optional[Span]]:
    """The names this unit declares itself, each with the span that declares it.

    The walk is `ast_walk.declarations`, the one home for "what does this unit
    declare". A second list here read the concrete groups alone and lost every generic
    one the collect pass had re-filed (#631).

    The span is OPTIONAL: a declaration the source did not write has none to give. No
    source reaches that today, because this pass runs before `instantiate` and a
    library-injected declaration keeps its own span, but the type says what the map
    holds and the caller answers for it (#714).
    """
    names: Dict[str, Optional[Span]] = {}
    for kind, node in declarations(program):
        if kind in _INNER_KINDS:
            continue
        name = getattr(node, "name", None)
        if isinstance(name, str) and name not in names:
            names[name] = getattr(node, "name_span", None) or getattr(node, "loc", None)
    for block in program.externals or ():
        names.setdefault(block.namespace, block.loc)
    return names


def _reject_alias_collision(reporter: Reporter, table: NamespaceTable,
                            declared: Dict[str, Optional[Span]],
                            use_stmt: UseStatement, alias: str) -> bool:
    """CE3013: one name holds one namespace. True when the alias was refused.

    Each arm that names a second location points at it or says it in prose. A `note`
    with no span renders as a relational diagnostic with one location, which the
    diagnostic ladder refuses (#714).
    """
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
            diagnostic = diagnostic.note_at("first bound here", bound_at)
        else:
            diagnostic = diagnostic.help(
                "an `unsafe external` block already binds this namespace")
        diagnostic.emit()
        return True
    if alias in declared:
        diagnostic = er.emit_with(reporter, er.ERR.CE3013, span, alias=alias)
        declared_at = declared[alias]
        if declared_at is not None:
            diagnostic = diagnostic.note_at(
                "this unit declares the name here", declared_at)
        else:
            diagnostic = diagnostic.help("this unit already declares the name")
        diagnostic.emit()
        return True
    return False


def _provider_for(use_stmt: UseStatement, tables: SymbolTables, units: Dict[str, Unit],
                  host: Optional[Unit] = None,
                  visited: AbstractSet[str] = frozenset()) -> Provider:
    """What one `use` brings. A provider, never a written path (section 3.1).

    `visited` is the chain of units whose `public use` statements led here, so a cycle
    of re-exports builds a finite provider (Ruling 7, rule 2).
    """
    if use_stmt.is_library:
        return _library_provider(use_stmt.path, tables, units, visited,
                                 library_name=use_stmt.library_name)
    if use_stmt.is_stdlib:
        return _stdlib_provider(use_stmt.path, tables, units, visited)
    return _unit_provider(_imported_unit(use_stmt.path, host, units), tables,
                          units=units, visited=visited)


def _imported_unit(path: str, host: Optional[Unit], units: Dict[str, Unit]) -> str:
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


def _unit_provider(unit_name: str, tables: SymbolTables,
                   homed: Optional[Dict[str, str]] = None, *,
                   units: Optional[Dict[str, Unit]] = None,
                   visited: AbstractSet[str] = frozenset(),
                   generics: Tuple[Provider, ...] = ()) -> UnitNamespace:
    """A compilation unit's own declarations, from the collect pass's tables.

    `homed` is the predefined enums a STDLIB unit is the home of (#574): no unit
    declares `FileMode`, so the visibility table has no record to read it from. The
    unit's `public use` statements become its re-exports (section 8.1), and so does the
    built-in generic a stdlib unit activates (`generics`, `<collections/iter>`).
    """
    declared = [
        (kind, name, origin.is_public)
        for (kind, name), origin in getattr(tables.visibility, "by_key", {}).items()
        if origin.unit_name == unit_name and kind in _MEMBER_ONLY_KINDS
    ]
    others = {name: kind for kind, name, _ in declared}
    others.update(homed or {})
    return UnitNamespace(
        unit_name,
        functions=dict(tables.funcs.by_unit.get(unit_name, {})),
        constants=dict(tables.constants.by_unit.get(unit_name, {})),
        generics=dict(tables.generic_funcs.by_unit.get(unit_name, {})),
        others=others,
        reexports=(*_reexports_of(unit_name, tables, units, visited),
                   *generics),
        hidden={name for _, name, is_public in declared if not is_public},
    )


def _reexports_of(unit_name: str, tables: SymbolTables,
                  units: Optional[Dict[str, Unit]],
                  visited: AbstractSet[str]) -> Tuple[Provider, ...]:
    """The providers a unit's `public use` statements name, in written order.

    Read off the unit's own AST, so a source library's unit and a bundled stdlib module
    re-export exactly as a user unit does. A unit already on the chain contributes
    nothing a second time, which is what makes a cycle terminate; a unit with no AST has
    no statement to read.
    """
    if units is None or unit_name in visited:
        return ()
    unit = units.get(unit_name)
    program = getattr(unit, "ast", None)
    if program is None:
        return ()
    chain = frozenset(visited) | {unit_name}
    return tuple(
        _provider_for(use_stmt, tables, units, unit, chain)
        for use_stmt in program.uses or ()
        if use_stmt.is_public and use_stmt.alias is None
    )


def _stdlib_provider(path: str, tables: SymbolTables, units: Dict[str, Unit],
                     visited: AbstractSet[str] = frozenset()) -> Provider:
    """One of the standard library's four shapes (section 4.3)."""
    from sushi_lang.semantics.stdlib_registry import (
        get_stdlib_registry, is_source_stdlib_module,
    )

    # The predefined enums this module is the HOME of ride along with every shape
    # (#574): `<io/fs>` brings `FileMode`, `<sys/env>` brings `EnvError`.
    homed = homed_enums(path, getattr(tables, "enums", None))
    generic = GENERIC_UNIT_TYPES.get(path)
    gated = () if generic is None else (GenericNamespace(path, {generic}),)
    module = get_stdlib_registry().get_module(path)
    if is_source_stdlib_module(path):
        # A source module that also activates a built-in generic hands the name on as
        # a re-export of its unit: one provider, both roles. A module with two halves
        # (`<math>`) hands on the names of its generator half the same way.
        generator_half = (() if module is None
                          else (StdlibNamespace(path, module, types=homed),))
        return _unit_provider(path, tables, homed, units=units, visited=visited,
                              generics=(*gated, *generator_half))

    if gated:
        return gated[0]

    if module is not None:
        # A registry module declares its re-exports beside its functions (section
        # 8.1): `<io/files>` hands on `<io/error>`, so `FileError` rides with `fd_*`.
        chain = frozenset(visited) | {path}
        reexports = tuple(
            _stdlib_provider(other, tables, units, chain)
            for other in getattr(module, "reexports", ()) if other not in chain)
        return StdlibNamespace(path, module, types=homed, reexports=reexports)

    # A method interface: the import enables methods on a type and brings no name.
    # CW3004 is what says so, at the `use` rather than at every call after it.
    return MethodInterfaceNamespace(path, homed)


def _library_provider(path: str, tables: SymbolTables, units: Dict[str, Unit],
                      visited: AbstractSet[str] = frozenset(),
                      library_name: Optional[str] = None) -> Provider:
    """One unit of a library. The namespace is the unit, never the library (section 8).

    `library_name` is the name stamped into the `.slib` the path found (#1120). It
    picks the library, and the library's unit is the one the path's last segment
    names, or its only unit: a `.slib` built under another name than its unit's is the
    same library. Without the stamp, the unit is matched on the last segment alone.
    """
    wanted = path.rsplit("/", 1)[-1]

    source_units = [name for name, unit in units.items()
                    if getattr(unit, "from_library", False)]
    if library_name is not None:
        own = [name for name in source_units
               if name.startswith(f"lib/{library_name}/")]
        matched = _one_of(own, lambda name: name.rsplit("/", 1)[-1] == wanted)
        if matched is None and len(own) == 1:
            matched = own[0]
    else:
        matched = _one_of(source_units, lambda name: name.rsplit("/", 1)[-1] == wanted)
    if matched is not None:
        return _unit_provider(matched, tables, units=units, visited=visited)

    return UnitNamespace(path, functions={}, constants={})


def _one_of(items: Iterable[str], predicate: Callable[[str], bool]) -> Optional[str]:
    """The single item the predicate accepts, or None when it is not exactly one."""
    matches: Tuple[str, ...] = tuple(name for name in items if predicate(name))
    return matches[0] if len(matches) == 1 else None
