"""The `--warn-unused` lint: a private declaration nothing reaches (CW1004), and an
import whose unit names nothing it brings (CW3006).

A private name is visible only in its own unit, so both questions are asked per unit, over
the WRITTEN declarations. The names a declaration mentions are every string its nodes hold
and every name the types in it spell, string literals left out. That over-approximates on
purpose: a local that happens to spell a private function's name keeps the function
alive, so the lint can miss a dead declaration and never reports a live one.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Set, Tuple, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import (
    DestructureTarget, FuncDef, Node, Param, Program, StringLit, UseStatement)
from sushi_lang.semantics.ast_walk import (
    DESCENDED_FIELD_KINDS, declarations, field_kind, node_fields, signature_constraints,
    signature_types, walk_nodes)
from sushi_lang.semantics.type_walk import spelled_names
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.units import Unit
from sushi_lang.semantics.visibility import CONVERSION, EXTENSION_METHOD, kind_word

# A kind that holds no name of its own and is reached through a receiver, so it is a root.
_ROOT_KINDS = frozenset({EXTENSION_METHOD, CONVERSION, "perk implementation",
                         "external block"})
_CHECKED_KINDS = frozenset({"constant", "variable", "struct", "enum", "perk", "function"})
# The one stdlib-method import, as the per-unit CE3015 check reads it.
_METHOD_MODULE = "collections/strings"


def check_unused(reporter: Reporter, unit: Unit, tables: SymbolTables,
                 units: Dict[str, Unit]) -> None:
    """Report the dead private declarations and the unused imports of one unit."""
    program = cast(Program, unit.ast)
    written = [(kind, decl) for kind, decl in declarations(program)
               if kind in _ROOT_KINDS or (kind in _CHECKED_KINDS and _is_written(decl))]
    mentions = _mentions(program, written)
    _report_dead(reporter, written, mentions)
    used = set().union(*mentions.values())
    for use_stmt in program.uses or ():
        if not (use_stmt.is_public or use_stmt.is_library) and not (
                _brings(use_stmt, unit, tables, units) & used):
            er.emit(reporter, er.ERR.CW3006, use_stmt.loc, import_=_spelled(use_stmt))


def _report_dead(reporter: Reporter, written: List[Tuple[str, Any]],
                 mentions: Dict[int, Set[str]]) -> None:
    by_name: Dict[str, List[Any]] = {}
    for kind, decl in written:
        if kind in _CHECKED_KINDS:
            by_name.setdefault(decl.name, []).append(decl)
    live = {id(decl) for kind, decl in written if _is_root(kind, decl)}
    queue = [decl for _, decl in written if id(decl) in live]
    while queue:
        for name in mentions[id(queue.pop())]:
            for decl in by_name.get(name, ()):
                if id(decl) not in live:
                    live.add(id(decl))
                    queue.append(decl)
    for kind, decl in written:
        if id(decl) not in live:
            er.emit(reporter, er.ERR.CW1004,
                    getattr(decl, "name_span", None) or getattr(decl, "loc", None),
                    kind=kind_word(kind, decl), name=decl.name)


def _is_written(decl: object) -> bool:
    """A declaration the source wrote, not one the compiler or a library put here."""
    return not (getattr(decl, "is_synthesized", False)
                or getattr(decl, "is_library_template", False)
                or getattr(decl, "link_symbol", None))


def _is_root(kind: str, decl: object) -> bool:
    if kind in _ROOT_KINDS or getattr(decl, "is_public", False):
        return True
    return isinstance(decl, FuncDef) and decl.name == "main"


def _mentions(program: Program, written: List[Tuple[str, Any]]) -> Dict[int, Set[str]]:
    """The names each written declaration mentions, keyed by the declaration.

    Three seams, and nothing else: the node walk for what a node holds (a name, a type, a
    parameter), and the two signature walks for the records a declaration keeps outside
    any node (a field, a variant, a perk method, a constraint).
    """
    mentions: Dict[int, Set[str]] = {id(decl): set() for _, decl in written}
    for _, decl in written:
        walk_nodes(decl, _reader(mentions[id(decl)]))
    for site in signature_types(program):
        if id(site.decl) in mentions:
            mentions[id(site.decl)].update(spelled_names(site.ty))
    for constraint in signature_constraints(program):
        if id(constraint.decl) in mentions:
            names = mentions[id(constraint.decl)]
            names.add(constraint.perk_name)
            if constraint.namespace is not None:
                names.add(constraint.namespace)
    return mentions


def _reader(names: Set[str]) -> Callable[[Node], bool]:
    """A node-walk visitor that reads the names each node's fields hold."""
    def visit(node: Node) -> bool:
        if isinstance(node, StringLit):
            return False
        for _field, value in node_fields(node):
            _read_value(value, names)
        return True
    return visit


def _read_value(value: object, names: Set[str]) -> None:
    kind = field_kind(value)
    if kind == "node":
        return
    if kind in DESCENDED_FIELD_KINDS:
        for item in cast(Iterable[object], value):
            _read_value(item, names)
    elif isinstance(value, str):
        names.add(value)
    elif isinstance(value, Param):
        names.update(spelled_names(value.ty))
    elif isinstance(value, DestructureTarget):
        names.update(spelled_names(value.ty))
        for nested in value.nested or ():
            _read_value(nested, names)
    else:
        names.update(spelled_names(value))


def _brings(use_stmt: UseStatement, unit: Unit, tables: SymbolTables,
            units: Dict[str, Unit]) -> Set[str]:
    """Every name that, written in `unit`, would be answered through this import."""
    from sushi_lang.semantics.passes.namespaces import _provider_for
    provider = _provider_for(use_stmt, tables, units, None, unit)
    if use_stmt.alias is not None:
        names = {use_stmt.alias}
    else:
        names = set(provider.members())
    for reached in provider.reaches():
        names.update(_methods_of(reached.origin, units))
    return names


def _methods_of(origin: str, units: Dict[str, Unit]) -> Set[str]:
    """The method names a reached unit or module makes callable with no name to write."""
    names: Set[str] = set()
    if _METHOD_MODULE == origin or _METHOD_MODULE.startswith(origin + "/"):
        from sushi_lang.sushi_stdlib.src.collections.strings import METHOD_SPECS
        names.update(METHOD_SPECS)
    program = getattr(units.get(origin), "ast", None)
    if program is None:
        return names
    names.update(ext.name for ext in [*program.extensions, *program.generic_extensions])
    for impl in [*program.perk_impls, *program.generic_perk_impls]:
        names.update(method.name for method in impl.methods)
    for perk in program.perks:
        names.update(method.name for method in perk.methods)
    return names


def _spelled(use_stmt: UseStatement) -> str:
    return f"<{use_stmt.path}>" if use_stmt.is_stdlib or use_stmt.is_library \
        else f'"{use_stmt.path}"'
