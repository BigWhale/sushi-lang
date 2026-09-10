"""Which parse node is an expression, and where a statement keeps its own."""
from __future__ import annotations
from typing import Optional, Tuple
from lark import Tree

from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree, ice


# Every node an expression can present at its ROOT. It is the closure of the grammar's
# `expr` rule over the rules that inline (`?`) and the ones that alias, and
# `tests/unit/test_expression_nodes_match_the_grammar.py` reads that closure out of the
# parser and refuses a set that has drifted from it. There was a second copy of this
# list, and the two disagreed: one held `cast` and the other did not, while neither held
# `range` or `borrow`. Both were still right, because `or_expr` carried no `?` and so
# stood over every expression the grammar could build.
EXPR_NODES = frozenset({
    "or_expr",
})


def statement_expr(container: Tree) -> Optional[Tree]:
    """The expression a statement holds, read from its DIRECT children.

    Each rule that calls this holds the expression at one fixed position and holds at
    most one, so there is nothing to disambiguate and no subtree to search. A `let`
    holds a written type beside it; the type node names are disjoint from `EXPR_NODES`
    -- the same gate pins that -- and the type always stands first, so the scan reads
    from the back.
    """
    for child in reversed(container.children):
        if isinstance(child, Tree) and child.data in EXPR_NODES:
            return child
    return None


def expr_and_block(container: Tree) -> Tuple[Tree, Tree]:
    """The condition and the body of an `if`, an `elif` or a `while`."""
    blk = first_tree(container.children, "block")
    if blk is None:
        ice(container, "clause missing block")

    expr = statement_expr(container)
    if expr is None:
        ice(container, "clause missing condition expr")
    return expr, blk
