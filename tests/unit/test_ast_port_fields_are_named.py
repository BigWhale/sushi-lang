"""Two AST fields stay past Gate 4 as stated port templates: `ownership_provenance` and
`iterator_leaves`.

David's ruling of 2026-11-05 lets both fields live until group C3 removes them. This gate
lists every function that writes or reads either field. A new writer or reader fails the
gate. Task 88 of group C3 deletes both fields and this gate.

The scan reads the compiler's Python source. A site is an attribute access, a `setattr`
or a `getattr` that names the field as a string. The dataclass declarations in
`semantics/ast.py` are not sites.
"""
from __future__ import annotations

import ast
from pathlib import Path

import sushi_lang

PACKAGE = Path(sushi_lang.__file__).parent

#: field -> the group that deletes it
DELETED_BY = {"ownership_provenance": "C3", "iterator_leaves": "C3"}

#: (module path under sushi_lang/, qualified function name) -> the fields it names
SITES: dict[tuple[str, str], frozenset[str]] = {
    ("semantics/passes/borrow/iterators.py", "take_lent_iterator"):
        frozenset({"ownership_provenance"}),
    ("backend/runtime/closures.py", "emit_lambda"): frozenset({"ownership_provenance"}),
    ("semantics/generics/iterators.py", "state_on_heap"): frozenset({"iterator_leaves"}),
}

#: The rows of `SITES` at the day of the pin. A row may leave both; none may join.
PINNED_SITES: frozenset[tuple[str, str]] = frozenset({
    ("semantics/passes/borrow/iterators.py", "take_lent_iterator"),
    ("backend/runtime/closures.py", "emit_lambda"),
    ("semantics/generics/iterators.py", "state_on_heap"),
})

_ACCESSORS = {"setattr", "getattr", "hasattr", "delattr"}


def _named_fields(node: ast.AST) -> set[str]:
    found: set[str] = set()
    if isinstance(node, ast.Attribute) and node.attr in DELETED_BY:
        found.add(node.attr)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _ACCESSORS and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in DELETED_BY):
        found.add(node.args[1].value)
    return found


def _visit(node: ast.AST, scope: str, found: dict[str, set[str]]) -> None:
    for child in ast.iter_child_nodes(node):
        inner = scope
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = f"{scope}.{child.name}" if scope else child.name
        fields = _named_fields(child)
        if fields:
            found.setdefault(scope or "<module>", set()).update(fields)
        _visit(child, inner, found)


def _scan() -> dict[tuple[str, str], frozenset[str]]:
    sites: dict[tuple[str, str], frozenset[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        found: dict[str, set[str]] = {}
        _visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), "", found)
        rel = path.relative_to(PACKAGE).as_posix()
        for scope, fields in found.items():
            sites[(rel, scope)] = frozenset(fields)
    return sites


def test_every_site_of_a_port_field_is_named():
    new = {key: sorted(fields) for key, fields in _scan().items()
           if fields - SITES.get(key, frozenset())}
    assert not new, (
        "ownership_provenance and iterator_leaves are stated port templates (David's "
        "ruling of 2026-11-05). Group C3 (Task 88) deletes them. No new writer or reader "
        f"may join. These sites name one of them: {new}")


def test_every_named_site_still_exists():
    scanned = _scan()
    stale = sorted(key for key, fields in SITES.items()
                   if fields - scanned.get(key, frozenset()))
    assert not stale, f"delete these rows of SITES together with the code: {stale}"


def test_no_site_row_is_new():
    grown = sorted(set(SITES) - PINNED_SITES)
    assert not grown, f"a site row may be deleted and never added: {grown}"
    stale = sorted(PINNED_SITES - set(SITES))
    assert not stale, f"delete the pin row together with the site row: {stale}"
