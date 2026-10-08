"""One predicate answers "does this unrolled `expand` element end every path" (#1070).

A copy of a pack template holds each element in place in its block, so an element
statement that ends every path can stand before more statements of that block. The
borrow pass does not walk them and the backend does not emit them. Both ask
`ends_unrolled_run`, in `semantics/ast_walk.py`: a neutral home, so the backend does not
import the borrow pass. A second definition, an import from another module, or a body
that reads `expand_copies` and asks `terminates` by hand is a second answer that can
disagree with the first.
"""
from __future__ import annotations

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "sushi_lang"
HOME = "semantics/ast_walk.py"
HOME_MODULE = "sushi_lang.semantics.ast_walk"
PREDICATE = "ends_unrolled_run"


def _trees():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield (path.relative_to(PACKAGE).as_posix(),
               ast.parse(path.read_text(encoding="utf-8")))


def _reads_copies_and_asks_terminates(fn: ast.AST) -> bool:
    reads = any(isinstance(n, ast.Attribute) and n.attr == "expand_copies"
                for n in ast.walk(fn))
    asks = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "terminates" for n in ast.walk(fn))
    return reads and asks


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


def test_no_body_checks_an_unrolled_run_end_by_hand():
    found = [(where, fn.name) for where, tree in _trees() for fn in ast.walk(tree)
             if isinstance(fn, ast.FunctionDef) and _reads_copies_and_asks_terminates(fn)]
    assert found == [(HOME, PREDICATE)], found


def test_the_detector_sees_a_hand_written_check():
    """The control: a detector that finds nothing would pass the row above for free."""
    tree = ast.parse("def stop(stmt):\n"
                     "    return bool(stmt.expand_copies) and terminates(stmt)\n")
    assert any(_reads_copies_and_asks_terminates(fn) for fn in ast.walk(tree)
               if isinstance(fn, ast.FunctionDef))
