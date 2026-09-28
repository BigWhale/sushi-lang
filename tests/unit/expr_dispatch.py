"""The readers that every expression-dispatch totality gate shares.

A dispatch over `Expr` is total when it names every member of the union, names nothing
outside it, and skips only leaves. These helpers read the union, the arms a function
names in its source, and the fields of a node that hold an expression.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing

from sushi_lang.semantics import ast as sushi_ast


def expr_union_types() -> set[type]:
    """Every node type in the `Expr` union (semantics/ast.py)."""
    return set(typing.get_args(sushi_ast.Expr))


def expr_union_names() -> set[str]:
    return {t.__name__ for t in expr_union_types()}


def dispatched_names(func, *, inert: tuple[type, ...] = (),
                     subject_is_a_name: bool = False) -> set[str]:
    """Every class name `func` dispatches on, read from its source.

    Reads both idioms: a `case Name():` class pattern (a `MatchOr` alternative is walked
    like any other) and an `isinstance()` call. A name `INERT_EXPRS` in the source is
    replaced by the members of `inert`.

    With `subject_is_a_name`, an `isinstance` counts only when its subject is a bare
    name: `isinstance(expr.callee, Name)` decides inside an arm that is already chosen,
    so it names no arm of its own.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.MatchClass):
            if isinstance(node.cls, ast.Name):
                names.add(node.cls.id)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "isinstance":
            if subject_is_a_name and not isinstance(node.args[0], ast.Name):
                continue
            target = node.args[1]
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Tuple):
                names |= {elt.id for elt in target.elts if isinstance(elt, ast.Name)}

    if "INERT_EXPRS" in names:
        names.discard("INERT_EXPRS")
        names |= {t.__name__ for t in inert}
    return names


def expression_fields(node_type: type) -> list[tuple[str, object]]:
    """The fields of `node_type` whose annotation names a member of `Expr`."""
    members = expr_union_names()
    found = []
    for field, hint in typing.get_type_hints(node_type, globalns=vars(sushi_ast)).items():
        referenced = {
            t.__name__ for t in typing.get_args(hint) if hasattr(t, "__name__")
        } | ({hint.__name__} if hasattr(hint, "__name__") else set())
        if referenced & members:
            found.append((field, hint))
    return found
