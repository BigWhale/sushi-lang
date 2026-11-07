"""One predicate answers "which statements of a block does a path reach" (ir.md 7.2).

A copy of a pack template holds each element in place in its block, so an element statement
that ends every path can stand before more statements of that block. The borrow pass does
not walk them and SLIR lowering does not lower them. Both ask `reached_statements`, in
`semantics/shir/paths.py`, and the one walk over SHIR (`walk`) asks it for the ownership
check. A second definition, or a body that asks `holding` and `terminates` by hand, is a
second answer that can disagree with the first.
"""
from __future__ import annotations

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "sushi_lang"
HOME = "semantics/shir/paths.py"
HOME_MODULE = "sushi_lang.semantics.shir.paths"
PREDICATE = "reached_statements"


def _trees():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield (path.relative_to(PACKAGE).as_posix(),
               ast.parse(path.read_text(encoding="utf-8")))


def _asks_by_hand(fn: ast.AST) -> bool:
    calls = [n.func for n in ast.walk(fn) if isinstance(n, ast.Call)]
    holds = any(isinstance(f, ast.Attribute) and f.attr == "holding" for f in calls)
    asks = any(isinstance(f, ast.Name) and f.id == "terminates" for f in calls)
    return holds and asks


def test_the_predicate_is_defined_once_in_its_home():
    defs = [where for where, tree in _trees() for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == PREDICATE]
    assert defs == [HOME], defs


def test_every_reader_imports_the_predicate_from_its_home():
    strays = [(where, node.module) for where, tree in _trees() for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom)
              and any(alias.name == PREDICATE for alias in node.names)
              and node.module != HOME_MODULE]
    assert not strays, strays


def test_no_body_checks_a_reached_statement_by_hand():
    found = [(where, fn.name) for where, tree in _trees() for fn in ast.walk(tree)
             if isinstance(fn, ast.FunctionDef) and _asks_by_hand(fn)]
    assert found == [(HOME, PREDICATE)], found


def test_the_detector_sees_a_hand_written_check():
    """The control: a detector that finds nothing would pass the row above for free."""
    tree = ast.parse("def stop(copies, block, stmt):\n"
                     "    return copies.holding(block, 0) and terminates(stmt)\n")
    assert any(_asks_by_hand(fn) for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef))
