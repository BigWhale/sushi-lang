"""Gate: the SHIR printer has one row for each class that a body can hold (docs/design/ir.md 11.3).

`--dump-shir` prints a body through ONE dispatch table, `PRINTERS` in
`sushi_lang/semantics/shir/printer.py`. A class with no row is a node or a part that the
dump cannot show, and a golden file then fails with an internal error (CE0154) and not
with a diff. So this gate holds three rules:

1. The table has a row for each class of the node modules: every node of `ShirExpr`,
   `ShirStmt` and `ShirPattern`, the block, each part that a node holds (a constant, a
   call target, an argument, an array run, a loop source, a binding mode, a destructure
   target, an arm) and each origin of a body. The id that a destructure holds, `LocalId`,
   has a row too. The bases and the three frame classes have none: `print_body` prints
   the frame.
2. The table has no row for a class outside that set, so no row stays for a class that
   is gone.
3. The dump does not depend on the order of a hash or on an address: the printer source
   calls no `id()`, no `hash()` and makes no `set`.

These tests read Python objects and source text. They do not run the compiler.
"""
from __future__ import annotations

import ast
import inspect
import types
import typing
from pathlib import Path

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.shir import body, exprs, nodes, patterns, printer, stmts
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId

#: The modules whose classes a body holds.
_NODE_MODULES: tuple[types.ModuleType, ...] = (nodes, exprs, stmts, patterns, body)

#: The bases of the nodes: no value has one of these types.
_BASES = {nodes.ShirNode, exprs.ExprNode}

#: The frame of a body: `print_body` prints it, and no node holds one.
_FRAME = {body.ShirBody, body.ShirParam, body.ShirLocal}

#: The ids that a node holds where a dispatch reads them: a destructure target.
_IDS = {LocalId}


def _classes_of(module: types.ModuleType) -> set[type]:
    return {obj for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__}


def _expected() -> set[type]:
    found = set().union(*(_classes_of(module) for module in _NODE_MODULES))
    return (found - _BASES - _FRAME) | _IDS


def _names(classes: set[type]) -> list[str]:
    return sorted(f"{cls.__module__}.{cls.__name__}" for cls in classes)


def test_every_class_that_a_body_holds_has_a_row() -> None:
    missing = _expected() - set(printer.PRINTERS)
    assert not missing, f"the SHIR printer has no row for: {_names(missing)}"


def test_the_table_has_no_row_for_another_class() -> None:
    extra = set(printer.PRINTERS) - _expected()
    assert not extra, f"the SHIR printer has a row for a class that no body holds: {_names(extra)}"


@pytest.mark.parametrize("union", [exprs.ShirExpr, stmts.ShirStmt, patterns.ShirPattern],
                         ids=["ShirExpr", "ShirStmt", "ShirPattern"])
def test_each_member_of_a_node_union_has_a_row(union: object) -> None:
    missing = set(typing.get_args(union)) - set(printer.PRINTERS)
    assert not missing, f"the SHIR printer has no row for: {_names(missing)}"


def test_every_row_is_callable() -> None:
    bad = [cls.__name__ for cls, render in printer.PRINTERS.items() if not callable(render)]
    assert not bad, f"these rows do not render: {bad}"


def test_a_class_with_no_row_is_an_internal_error() -> None:
    class NotANode:
        pass

    with pytest.raises(InternalCompilerError) as caught:
        printer.ShirPrinter(DeclNames()).show(NotANode())
    assert caught.value.code == "CE0154"


_SOURCE = Path(inspect.getfile(printer)).read_text(encoding="utf-8")
_UNSTABLE_CALLS = {"id", "hash", "set", "frozenset", "vars"}


def test_the_printer_reads_no_address_and_no_hash_order() -> None:
    calls = sorted(
        f"{node.func.id}:{node.lineno}" for node in ast.walk(ast.parse(_SOURCE))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in _UNSTABLE_CALLS)
    sets = sorted(f"set display:{node.lineno}" for node in ast.walk(ast.parse(_SOURCE))
                  if isinstance(node, (ast.Set, ast.SetComp)))
    assert not calls + sets, f"the printer output must not depend on these: {calls + sets}"
