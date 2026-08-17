"""`semantics/ast.py`'s `__all__` must name every public AST node. No invisible nodes."""
from __future__ import annotations

import ast
import pathlib

import sushi_lang.semantics.ast as sushi_ast

AST_SOURCE = pathlib.Path(sushi_ast.__file__)


def _public_classes() -> set[str]:
    tree = ast.parse(AST_SOURCE.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }


def test_every_public_ast_class_is_exported():
    missing = sorted(_public_classes() - set(sushi_ast.__all__))
    assert not missing, (
        f"these AST node classes are not in __all__: {missing}.\n"
        "A pass doing `from ...ast import *` cannot see them, so it cannot handle them. "
        "This is how #174 happened -- Spread was invisible, so nothing dispatched on it."
    )


def test_all_names_actually_exist():
    """The mirror: a name in __all__ that does not exist breaks `import *` outright."""
    dangling = sorted(n for n in sushi_ast.__all__ if not hasattr(sushi_ast, n))
    assert not dangling, f"__all__ names symbols that do not exist: {dangling}"
