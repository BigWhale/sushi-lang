"""Lambda literal (closure) AST building.

A lambda is an expression (an `atom` alternative), so this parser lives beside the
other expression builders (`literals.py`, `calls.py`), not under `declarations/`.
It reuses the shared type/expr/block builders on `ASTBuilder`.

Grammar (see grammar.lark):
    lambda_expr:  "|" <params> "|" <expr>
    lambda_block: "|" <params> "|" [lambda_ret] <block>
    <params>    = lambda_no_params (`~`) | lambda_params(lambda_typed_param|lambda_bare_param ...)
    lambda_ret  = "->" type ["|" type]
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Union
from lark import Tree

from sushi_lang.semantics.ast import Lambda, Param, Block, Expr
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def _is_type_node(node) -> bool:
    return isinstance(node, Tree) and (node.data in TYPE_NODE_NAMES or node.data == "name_t")


def parse_lambda(t: Tree, ast_builder: "ASTBuilder") -> Lambda:
    """Build a Lambda from a `lambda_expr` / `lambda_block` parse tree."""
    is_block = t.data == "lambda_block"
    params = _parse_lambda_params(t, ast_builder)

    ret: Optional[object] = None
    err_type: Optional[object] = None
    body: Union[Expr, Block]

    if is_block:
        ret_node = first_tree(t.children, "lambda_ret")
        if ret_node is not None:
            type_children = [c for c in ret_node.children if _is_type_node(c)]
            if type_children:
                ret = ast_builder._parse_type(type_children[0])
            if len(type_children) > 1:
                err_type = ast_builder._parse_type(type_children[1])
        block_node = first_tree(t.children, "block")
        body = ast_builder._block(block_node)
    else:
        # Expression body is the trailing child (the body expr tree).
        body = ast_builder._expr(t.children[-1])

    return Lambda(
        params=params,
        body=body,
        is_block_body=is_block,
        ret=ret,
        err_type=err_type,
        loc=span_of(t),
    )


def _parse_lambda_params(t: Tree, ast_builder: "ASTBuilder") -> List[Param]:
    """Extract the lambda's parameter list (empty for the `|~|` zero-param form)."""
    if first_tree(t.children, "lambda_no_params") is not None:
        return []
    params_node = first_tree(t.children, "lambda_params")
    if params_node is None:
        return []

    out: List[Param] = []
    for ch in params_node.children:
        if not isinstance(ch, Tree):
            continue
        if ch.data == "lambda_typed_param":
            ty_node = next((c for c in ch.children if _is_type_node(c)), None)
            nm_tok = first_name(ch.children)
            ty = ast_builder._parse_type(ty_node) if ty_node is not None else None
            out.append(Param(
                name=str(nm_tok),
                ty=ty,
                name_span=span_of(nm_tok),
                type_span=span_of(ty_node) if ty_node is not None else None,
                loc=span_of(ch),
            ))
        elif ch.data == "lambda_bare_param":
            # A bare-name param: type is inferred later from an expected FunctionType.
            nm_tok = first_name(ch.children)
            out.append(Param(
                name=str(nm_tok),
                ty=None,
                name_span=span_of(nm_tok),
                loc=span_of(ch),
            ))
    return out
