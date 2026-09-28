"""`TYPE_NODE_NAMES` must hold every type node the grammar can produce, and one
predicate must be the only reader of it.

Seventeen readers asked "is this child a written type", and sixteen of them added
`name_t` to the set by hand -- in five different spellings. The seventeenth
(`foreach`) did not, so a `foreach` with a bare user type in the item position stopped
with an internal error while every other position took the same name (#595). The set is
now complete and `is_type_node` is the one question, so the two halves of that fault
each have a gate: the set against the grammar
(`test_expression_nodes_match_the_grammar.py`), and the readers against the set (here).
"""
from __future__ import annotations

from pathlib import Path

from lark import Token, Tree

from sushi_lang.semantics.ast_builder.utils.tree_navigation import is_type_node

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AST_BUILDER = PROJECT_ROOT / "sushi_lang" / "semantics" / "ast_builder"


def test_one_reader_of_the_set():
    """`is_type_node` is the question. A second spelling is how a member goes missing."""
    readers = sorted(
        p.relative_to(PROJECT_ROOT).as_posix()
        for p in AST_BUILDER.rglob("*.py")
        if "TYPE_NODE_NAMES" in p.read_text()
    )
    assert readers == ["sushi_lang/semantics/ast_builder/utils/tree_navigation.py"], (
        f"the set is read in more than one place: {readers}. "
        "Ask `is_type_node(node)` instead -- it cannot forget a member."
    )


def test_a_bare_name_is_a_type_node():
    """The member that was missing, asserted directly."""
    assert is_type_node(Tree("name_t", [Token("NAME", "Point")]))
    assert is_type_node(Tree("qualified_name_t", [Token("NAME", "geo"), Token("NAME", "Vec")]))
    assert not is_type_node(Tree("block", []))
    assert not is_type_node(Token("NAME", "Point"))
