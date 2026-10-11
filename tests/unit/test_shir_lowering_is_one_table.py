"""Gate: body lowering dispatches on the AST class through ONE table (docs/design/ir.md 6.3, 7.10).

`LOWERING` in `sushi_lang/semantics/shir/lower_nodes.py` gives the lowering of each AST
expression class and each AST statement class. A class with no row is a HOLE: lowering
records it and the compile goes on, or the stop code CE0155 stops it under
`SUSHI_SHIR_STRICT=1`. A call argument has a second table, `ARGUMENTS`: the classes that
are the FORM of an argument and no value of their own there, a bloom `xs...` and a
`peek`/`poke` marker. The totality gate of a later chunk reads both tables, so this gate
holds four rules:

1. Each key of each table is an AST body class: a member of the `Expr` union or a
   statement class. A pattern is lowered with its `Match`, not through this table.
2. Each row that lowering has today is in its table. The sets only grow.
3. No module of the lowering tests an AST body class with `isinstance`: a second
   dispatch beside the tables is a construct that the totality gate cannot see.
4. Each row is callable.

These tests read Python objects and source text. They do not run the compiler.
"""
from __future__ import annotations

import ast
import inspect
import typing
from pathlib import Path

from sushi_lang.semantics import ast as a
from sushi_lang.semantics.shir import lower_nodes, lowering

#: The AST classes that lowering handles. A chunk that lowers a class adds it here.
LOWERED: frozenset[str] = frozenset({
    "IntLit", "FloatLit", "BoolLit", "StringLit", "BlankLit",
    "Name", "UnaryOp", "BinaryOp",
    "Call", "MethodCall", "DotCall",
    "ExprStmt", "Return", "Print", "PrintLn",
})

#: The AST classes that are the form of a call argument (`ARGUMENTS`). The set only grows.
ARGUMENT_FORMS: frozenset[str] = frozenset({"Spread", "Borrow"})

#: The modules of body lowering.
_MODULES = (lowering, lower_nodes)


def _statement_classes() -> set[type]:
    found: set[type] = set()
    pending = [a.Stmt]
    while pending:
        for sub in pending.pop().__subclasses__():
            if sub not in found:
                found.add(sub)
                pending.append(sub)
    return found


def _body_classes() -> set[type]:
    return set(typing.get_args(a.Expr)) | _statement_classes()


def test_every_key_is_an_ast_body_class() -> None:
    keys = set(lower_nodes.LOWERING) | set(lower_nodes.ARGUMENTS)
    stray = sorted(cls.__name__ for cls in keys - _body_classes())
    assert not stray, f"a lowering table has a row for a class that is no AST body class: {stray}"


def test_every_lowered_class_has_a_row() -> None:
    names = {cls.__name__ for cls in lower_nodes.LOWERING}
    missing = sorted(LOWERED - names)
    assert not missing, f"the lowering table has no row for: {missing}"


def test_the_table_is_the_lowered_set() -> None:
    names = {cls.__name__ for cls in lower_nodes.LOWERING}
    extra = sorted(names - LOWERED)
    assert not extra, f"add these classes to LOWERED in this gate: {extra}"


def test_the_argument_table_is_the_argument_forms() -> None:
    names = {cls.__name__ for cls in lower_nodes.ARGUMENTS}
    assert names == ARGUMENT_FORMS, (
        f"the argument table and ARGUMENT_FORMS differ: {sorted(names ^ ARGUMENT_FORMS)}")


def test_every_row_is_callable() -> None:
    rows = [*lower_nodes.LOWERING.items(), *lower_nodes.ARGUMENTS.items()]
    bad = sorted(cls.__name__ for cls, row in rows if not callable(row))
    assert not bad, f"these rows do not lower: {bad}"


def _isinstance_tests_of_body_classes(module: object) -> list[str]:
    body_names = {cls.__name__ for cls in _body_classes()}
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))  # type: ignore[arg-type]
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance" and len(node.args) == 2):
            continue
        tested = {sub.id if isinstance(sub, ast.Name) else sub.attr
                  for sub in ast.walk(node.args[1]) if isinstance(sub, (ast.Name, ast.Attribute))}
        hits = sorted(tested & body_names)
        if hits:
            found.append(f"{Path(inspect.getfile(module)).name}:{node.lineno} {hits}")  # type: ignore[arg-type]
    return found


def test_no_second_dispatch_on_an_ast_body_class() -> None:
    found = [hit for module in _MODULES for hit in _isinstance_tests_of_body_classes(module)]
    assert not found, f"lowering tests an AST body class outside the table: {found}"
