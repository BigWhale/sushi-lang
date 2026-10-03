"""The `monomorphize` statement walk reaches every statement kind. No silent skips.

The walk over a substituted body was a hand-written ladder of statement arms. It had no
arm for `println`, `print` and a rebind, and it walked only a block arm of a `match`, so
a generic call there was not collected and the typecheck pass answered CE2061 for a
correct program (#1157). Now a statement that BINDS a name has an arm of its own, and
every other statement goes through the one node walk (`walk_nodes`).

Read from the source, because the walk runs inside the compiler: the arms the walk
names are the binding statements and nothing else, and the rest goes to the node walk.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from expr_dispatch import dispatched_names, expr_union_names
from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.generics.monomorphize import functions as mono
from sushi_lang.semantics.generics.monomorphize.functions import FunctionMonomorphizer

WALK = FunctionMonomorphizer._collect_block_instantiations
NODE_WALK = FunctionMonomorphizer._collect_from_statement_nodes

# The statements that BIND a name for what follows them or for a body they hold. Each
# has an arm of its own in the walk. A new binding statement is added here, on purpose.
BINDING_STATEMENTS = {"Let", "Foreach", "Match"}


def _statement_kinds() -> set[str]:
    """Every statement class the AST declares."""
    out: set[str] = set()
    pending = [sushi_ast.Stmt]
    while pending:
        for sub in pending.pop().__subclasses__():
            out.add(sub.__name__)
            pending.append(sub)
    return out


def _calls(func) -> set[str]:
    """Every name `func` calls, as a plain name or as an attribute."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_the_binding_statements_are_statement_kinds():
    stray = BINDING_STATEMENTS - _statement_kinds()
    assert not stray, f"BINDING_STATEMENTS names no statement kind: {sorted(stray)}"


def test_the_walk_names_the_binding_statements_and_nothing_else():
    """A new arm for a statement that binds nothing is a second path: refuse it."""
    statement_arms = dispatched_names(WALK, subject_is_a_name=True) & _statement_kinds()
    assert statement_arms == BINDING_STATEMENTS, (
        f"the statement walk has arms for {sorted(statement_arms)}, and "
        f"the gate names {sorted(BINDING_STATEMENTS)}. A statement that binds no name "
        "goes through the node walk; a statement that binds one is named in "
        "BINDING_STATEMENTS here and has an arm."
    )


def test_every_other_statement_goes_through_the_node_walk():
    assert "_collect_from_statement_nodes" in _calls(WALK)
    assert "walk_nodes" in _calls(NODE_WALK)


def test_the_node_walk_hands_every_expression_to_the_expression_collector():
    """The expression collector is held total over `Expr` by its own gate."""
    handed = {kind.__name__ for kind in mono.EXPRESSION_KINDS}
    assert handed == expr_union_names()
    assert "_collect_from_expr" in _calls(NODE_WALK)
