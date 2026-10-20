"""`TypeInferenceVisitor` must have an arm for every AST expression kind. No silent skips.

`visitors.NodeVisitor.visit` builds the method name from the class name and falls back to
`generic_visit`. The inference visitor answers None there, so an expression kind with no
arm gets NO type and the fault surfaces far away (#639). Since the switch to SHIR the
visitor is the inference of the early passes alone (`ReadOnlyInferrer`); the body walk of
the typecheck pass has its own gate, `test_typecheck_table_is_total.py`.
"""
from __future__ import annotations

import typing

from sushi_lang.semantics import ast as a
from sushi_lang.semantics.passes.types.visit.inference import TypeInferenceVisitor


def _expr_kinds() -> set[str]:
    """Every node type in the `Expr` union (semantics/ast.py)."""
    return {t.__name__ for t in typing.get_args(a.Expr)}


def test_type_inference_covers_every_expression_kind():
    """The visitor extends NodeVisitor, so it inherits no arm at all."""
    missing = sorted(
        name for name in _expr_kinds()
        if not hasattr(TypeInferenceVisitor, f"visit_{name.lower()}")
    )
    assert not missing, (
        f"TypeInferenceVisitor has no visit_* arm for: {missing}. Its generic_visit "
        "answers None, so the expression gets NO type and the fault surfaces far away."
    )
