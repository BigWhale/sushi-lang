"""Gate: the SHIR lowering dispatch covers every AST body class (docs/design/ir.md 7.10, P1.15).

Lowering dispatches on the class of an AST node through three tables: `LOWERING` (an
expression and a statement) and `ARGUMENTS` (the form of a call argument) in
`sushi_lang/semantics/shir/lower_nodes.py`, and `PATTERNS` (a pattern position) in
`sushi_lang/semantics/shir/lower_patterns.py`. A class with no row is a hole: the dump
prints it, and under `SUSHI_SHIR_STRICT=1` it stops the compile with CE0155. So lowering is
total when every class that a body holds has a row. This gate holds four rules:

1. The union of the keys of the tables, plus `EXEMPT`, is the set of AST body classes: the
   members of the `Expr` union, the declared statement classes and the pattern classes,
   read from `semantics/ast.py`. A new AST body class with no row fails here.
2. Each entry of `EXEMPT` names an AST body class that has no row, with its reason. An
   entry for a class that is gone, or that a table now lowers, is stale and fails here.
3. The tables are every table that lowering keys on a class: each module-level
   `dict[type, ...]` of a lowering module is in `TABLES`, and each lookup `X.get(type(...))`
   in a lowering module reads a table of `TABLES`. A fourth table that this gate does not
   read fails here.
4. The check of rule 1 finds a class with no row: a stand-in class makes it fail.

The unnamed hole stays after this gate (`BodyLowerer.hole`, `Hole`): it is the backstop of
a class that has no row, and the error hole of a program that always has an error (an
unresolved name, a form that the language always refuses) is the same node.

These tests read Python objects and source text. They do not run the compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

from expr_dispatch import body_classes
from sushi_lang.semantics.shir import lower_nodes, lower_patterns

SHIR = Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics" / "shir"

#: The dispatch tables of lowering, by name.
TABLES: dict[str, dict[type, object]] = {
    "LOWERING": lower_nodes.LOWERING,
    "ARGUMENTS": lower_nodes.ARGUMENTS,
    "PATTERNS": lower_patterns.PATTERNS,
}

#: The AST body classes that no table lowers, each with its reason.
EXEMPT: dict[str, str] = {
    "EnumConstructor": (
        "no producer puts one in a body: the AST builder never makes one (a variant "
        "construction is a DotCall or a MemberAccess, and lowering gives EnumInit for it), "
        "and the typecheck pass makes one only as a view to validate a DotCall "
        "(passes/types/calls/dotcall.py), after lowering and never in the tree"),
}


def _lowering_modules() -> list[Path]:
    return sorted([SHIR / "lowering.py", *SHIR.glob("lower_*.py")])


def _names(classes: set[type]) -> set[str]:
    return {cls.__name__ for cls in classes}


def _uncovered(body: set[str], tables: dict[str, dict[type, object]],
               exempt: dict[str, str]) -> list[str]:
    covered = {cls.__name__ for table in tables.values() for cls in table} | set(exempt)
    return sorted(body - covered)


def test_every_ast_body_class_has_a_row_or_an_exemption() -> None:
    missing = _uncovered(_names(body_classes()), TABLES, EXEMPT)
    assert not missing, (
        f"no lowering table has a row for: {missing}. Give each a row in LOWERING, "
        "ARGUMENTS or PATTERNS, or an entry with its reason in EXEMPT of this gate.")


def test_no_table_has_a_row_for_another_class() -> None:
    body = _names(body_classes())
    stray = sorted(f"{name}:{cls.__name__}" for name, table in TABLES.items()
                   for cls in table if cls.__name__ not in body)
    assert not stray, f"a lowering table has a row for a class that is no AST body class: {stray}"


def test_no_exemption_is_stale() -> None:
    body = _names(body_classes())
    gone = sorted(set(EXEMPT) - body)
    assert not gone, f"EXEMPT names a class that is no AST body class: {gone}"
    lowered = sorted(set(EXEMPT) & {cls.__name__ for table in TABLES.values() for cls in table})
    assert not lowered, f"EXEMPT names a class that a table lowers; remove it: {lowered}"
    empty = sorted(name for name, reason in EXEMPT.items() if not reason.strip())
    assert not empty, f"an exemption has no reason: {empty}"


def _is_class_table(annotation: ast.expr) -> bool:
    """`dict[type, ...]`."""
    return (isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name)
            and annotation.value.id == "dict" and isinstance(annotation.slice, ast.Tuple)
            and isinstance(annotation.slice.elts[0], ast.Name)
            and annotation.slice.elts[0].id == "type")


def test_the_tables_are_every_class_table_of_lowering() -> None:
    declared = []
    for path in _lowering_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declared += [f"{path.name}:{node.target.id}" for node in tree.body
                     if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                     and _is_class_table(node.annotation)]
    names = sorted(entry.split(":")[1] for entry in declared)
    assert names == sorted(TABLES), (
        f"the class tables of lowering: {sorted(declared)}; this gate reads {sorted(TABLES)}")


def _is_class_lookup(node: ast.AST) -> bool:
    """`X.get(type(...))`."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and len(node.args) >= 1
            and isinstance(node.args[0], ast.Call) and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "type")


def test_every_lookup_by_class_reads_a_table_of_the_gate() -> None:
    lookups = []
    for path in _lowering_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if _is_class_lookup(node):
                table = node.func.value  # type: ignore[attr-defined]
                lookups.append((path.name, node.lineno,
                                table.id if isinstance(table, ast.Name) else ast.unparse(table)))
    assert lookups, "lowering looks up no table by class; the scan has gone stale"
    stray = [f"{name}:{line} {table}" for name, line, table in lookups if table not in TABLES]
    assert not stray, f"lowering looks up a table that this gate does not read: {stray}"


def test_a_class_with_no_row_fails_the_check() -> None:
    body = _names(body_classes()) | {"NewNode"}
    assert _uncovered(body, TABLES, EXEMPT) == ["NewNode"]
    assert _uncovered(_names(body_classes()), TABLES, {}) == sorted(EXEMPT)
