"""The `--warn-unused` lint: a private declaration nothing reaches (CW1004), and an
import whose unit names nothing it brings (CW3006).

A private name is visible only in its own unit, so both questions are asked per unit, over
the WRITTEN declarations. The names a declaration mentions are every string its subtree
holds, doc blocks and string literals left out. That over-approximates on purpose: a
local that happens to spell a private function's name keeps the function alive, so the
lint can miss a dead declaration and never reports a live one.
"""
from __future__ import annotations

import dataclasses
import enum
from typing import Any, Dict, Iterable, List, Set, Tuple, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import DocBlock, FuncDef, Program, StringLit, UseStatement
from sushi_lang.semantics.ast_walk import declarations
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.units import Unit

# A kind that holds no name of its own and is reached through a receiver, so it is a root.
_ROOT_KINDS = frozenset({"extension", "perk implementation", "external block"})
_CHECKED_KINDS = frozenset({"constant", "variable", "struct", "enum", "perk", "function"})
# The one stdlib-method import, as the per-unit CE3015 check reads it.
_METHOD_MODULE = "collections/strings"


def check_unused(reporter: Reporter, unit: Unit, tables: SymbolTables,
                 units: Dict[str, Unit]) -> None:
    """Report the dead private declarations and the unused imports of one unit."""
    program = cast(Program, unit.ast)
    written = [(kind, decl) for kind, decl in declarations(program)
               if kind in _ROOT_KINDS or (kind in _CHECKED_KINDS and _is_written(decl))]
    mentions = {id(decl): names_in(decl) for _, decl in written}
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
                    kind=kind, name=decl.name)


def _is_written(decl: object) -> bool:
    """A declaration the source wrote, not one the compiler or a library put here."""
    return not (getattr(decl, "is_synthesized", False)
                or getattr(decl, "is_library_template", False)
                or getattr(decl, "link_symbol", None))


def _is_root(kind: str, decl: object) -> bool:
    if kind in _ROOT_KINDS or getattr(decl, "is_public", False):
        return True
    return isinstance(decl, FuncDef) and decl.name == "main"


def names_in(root: object) -> Set[str]:
    """Every name a subtree mentions: each string it holds, doc blocks and literals left out."""
    names: Set[str] = set()
    seen: Set[int] = set()
    stack: List[object] = [root]
    while stack:
        obj = stack.pop()
        if isinstance(obj, str):
            names.add(str(obj))
            continue
        if obj is None or isinstance(obj, (int, float, enum.Enum, Span, DocBlock,
                                           StringLit)):
            continue
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        stack.extend(_children(obj))
    return names


def _children(obj: object) -> Iterable[object]:
    if isinstance(obj, (list, tuple, set, frozenset)):
        return obj
    if isinstance(obj, dict):
        return [*obj.keys(), *obj.values()]
    if dataclasses.is_dataclass(obj):
        return [getattr(obj, f.name, None) for f in dataclasses.fields(obj)]
    return list(getattr(obj, "__dict__", {}).values())


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
