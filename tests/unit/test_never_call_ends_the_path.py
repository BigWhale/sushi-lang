"""Every reader of the path predicate knows which call never returns (#1325).

A statement that is a call of a `!` function ends the path (docs/design/never-return.md).
The predicate of `semantics/shir/paths.py` learns it from the `calls` argument, the
typecheck answers of the body. A reader that gives no `calls` reads such a call as a
statement that goes on, and then disagrees with the other passes: the borrow pass joins a
path that SLIR lowering ended. So each call of a predicate outside its home gives `calls`.
"""
from __future__ import annotations

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "sushi_lang"
HOME = "semantics/shir/paths.py"
PREDICATES = {"terminates", "ends_the_path", "first_dead_statement", "reached_statements"}
# `reached_statements` takes `calls` as its third positional argument.
POSITIONAL = {"reached_statements": 3}


def _calls_of_predicates():
    for path in sorted(PACKAGE.rglob("*.py")):
        where = path.relative_to(PACKAGE).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if where == HOME or _defines_a_predicate(tree):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in PREDICATES):
                yield where, node


def _defines_a_predicate(tree: ast.AST) -> bool:
    """A module with a predicate of its own name (the AST `terminates` of `ast_walk.py`)
    reads no SHIR, and is not a reader of the predicate."""
    return any(isinstance(node, ast.FunctionDef) and node.name in PREDICATES
               for node in ast.walk(tree))


def _gives_calls(node: ast.Call) -> bool:
    if any(keyword.arg == "calls" for keyword in node.keywords):
        return True
    return len(node.args) >= POSITIONAL.get(node.func.id, 99)


def test_every_reader_gives_the_calls():
    missing = [(where, node.lineno) for where, node in _calls_of_predicates()
               if not _gives_calls(node)]
    assert not missing, missing


def test_the_scan_sees_the_readers():
    """The control: a scan that finds no reader would pass the row above for free."""
    found = {where for where, _node in _calls_of_predicates()}
    assert {"semantics/slir/lower.py", "semantics/passes/borrow/statements.py",
            "semantics/passes/types/control_flow.py"} <= found
