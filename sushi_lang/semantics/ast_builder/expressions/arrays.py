"""Array expression parsing (literals, new, from)."""
from __future__ import annotations
from typing import List, TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import Expr, ArrayLiteral, DynamicArrayNew, DynamicArrayFrom
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def array_literal(array_literal_node: Tree, ast_builder: 'ASTBuilder') -> ArrayLiteral:
    """Parse array_literal: \"[\" [array_elements] \"]\" """
    assert array_literal_node.data == "array_literal"

    elements: List[Expr] = []

    elements_node = first_tree(array_literal_node.children, "array_elements")
    if elements_node:
        for child in elements_node.children:
            if isinstance(child, Tree):
                elements.append(ast_builder._expr(child))

    return ArrayLiteral(
        elements=elements,
        loc=span_of(array_literal_node)
    )


def dynamic_array_new(t: Tree) -> DynamicArrayNew:
    """Parse dynamic_array_new: \"new\" \"(\" \")\" """
    assert t.data == "dynamic_array_new"
    return DynamicArrayNew(loc=span_of(t))


def dynamic_array_from(t: Tree, ast_builder: 'ASTBuilder') -> DynamicArrayFrom:
    """Parse dynamic_array_from: \"from\" \"(\" array_literal \")\" """
    assert t.data == "dynamic_array_from"

    array_literal_node = None
    for child in t.children:
        if isinstance(child, Tree) and child.data == "array_literal":
            array_literal_node = child
            break

    if array_literal_node is None:
        raise NotImplementedError("dynamic_array_from: missing array literal")

    array_lit = array_literal(array_literal_node, ast_builder)
    return DynamicArrayFrom(elements=array_lit, loc=span_of(t))
