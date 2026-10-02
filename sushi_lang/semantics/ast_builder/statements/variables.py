"""Variable statement parsing (let, rebind)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Optional
from lark import Tree
from itertools import count

from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.semantics.ast import (
    DestructureTarget, Expr, Let, Name, Rebind, Stmt, TupleLiteral)
from sushi_lang.semantics.hidden_names import hidden_name
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_name, first_token, first_tree, ice, is_type_node, mark_nom)
from sushi_lang.semantics.ast_builder.utils.expression_discovery import EXPR_NODES, statement_expr
from sushi_lang.internals.report import Span, span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_let_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Let:
    """Parse let_stmt: LET [type] NAME "=" NOM? expr"""
    nm = first_name(node.children)
    if nm is None:
        ice(node, "NAME missing")

    type_node = None
    for child in node.children:
        if is_type_node(child):
            type_node = child
            break

    ty = ast_builder._parse_type(type_node) if type_node else None

    lambda_block_node = first_tree(node.children, "lambda_block")
    if lambda_block_node is not None:
        from sushi_lang.semantics.ast_builder.expressions import lambdas
        value = lambdas.parse_lambda(lambda_block_node, ast_builder)
    else:
        expr_node = statement_expr(node)
        if expr_node is None:
            ice(node, "expression missing")
        value = ast_builder._expr(expr_node)
        nom = first_token(node.children, "NOM")
        if nom is not None:
            mark_nom(value, nom)

    return Let(
        name=str(nm),
        ty=ty,
        value=value,
        name_span=span_of(nm),
        type_span=span_of(type_node) if type_node else None,
        loc=span_of(node)
    )


# One counter for the process: each destructure binds the whole tuple under a name the
# grammar refuses, so it collides with nothing a user writes.
_destructure_ids = count()


def parse_let_destructure(node: Tree, ast_builder: 'ASTBuilder') -> Let:
    """Parse let_destructure: LET tuple_list "=" NOM? expr (docs/design/tuples.md).

    The `Let` binds the whole value under a hidden name with no span, so it is never
    CW1001, and its targets split it.
    """
    tuple_list = first_tree(node.children, "tuple_list")
    expr_node = statement_expr(node)
    if tuple_list is None or expr_node is None:
        ice(node, "let_destructure expects a tuple_list and an expression")
    value = ast_builder._expr(expr_node)
    nom = first_token(node.children, "NOM")
    if nom is not None:
        mark_nom(value, nom)
    return destructure_let(tuple_list, value, span_of(node), ast_builder)


def destructure_let(tuple_list: Tree, value: Expr, loc: Optional[Span],
                    ast_builder: 'ASTBuilder') -> Let:
    """The `Let` of a destructure: the whole value under a hidden name, split by targets.

    The `let` destructure and the `foreach` destructure both build it.
    """
    from sushi_lang.semantics.ast_builder.types.tuples import parse_destructure_targets

    return Let(
        name=hidden_name("tuple", next(_destructure_ids)),
        ty=None,
        value=value,
        targets=parse_destructure_targets(tuple_list, ast_builder),
        loc=loc,
    )


def parse_rebind_stmt(node: Tree, ast_builder: 'ASTBuilder') -> List[Stmt]:
    """Parse rebind_stmt: postfix ":=" expr"""
    target_node = first_tree(node.children, "maybe_call")
    if target_node is None:
        ice(node, "target missing")

    # `rebind_stmt: postfix ASSIGN_REBIND expr` holds two expressions, and the
    # target is the first, so the value is the other one.
    expr_node = None
    for child in node.children:
        if isinstance(child, Tree) and child.data in EXPR_NODES and child is not target_node:
            expr_node = child
            break

    if expr_node is None:
        ice(node, "value expression missing")

    target = ast_builder._expr(target_node)
    value = ast_builder._expr(expr_node)
    if isinstance(target, TupleLiteral):
        return destructuring_rebind(target, value, span_of(node), ast_builder)
    return [Rebind(target=target, value=value, loc=span_of(node))]


def destructuring_rebind(target: TupleLiteral, value: Expr, loc: Optional[Span],
                         ast_builder: 'ASTBuilder') -> List[Stmt]:
    """`(a, b) := v` (rulings 13 and 16 of docs/design/tuples.md).

    The desugar is a `let` destructure of `v` into hidden binders, then one plain rebind
    per target, from left to right: `a := #rb0`, `b := #rb1`. So the whole right side is
    evaluated first, each target is a rebind or a field write by the rule of `x := v`,
    and an owning swap moves each value and destroys nothing.
    """
    seen: Dict[str, Expr] = {}
    rebinds: List[Stmt] = []
    targets = _rebind_targets(target, rebinds, seen, ast_builder)
    whole = Let(name=hidden_name("tuple", next(_destructure_ids)), ty=None, value=value,
                targets=targets, loc=loc, rebinds=True)
    return [whole, *rebinds]


def _rebind_targets(target: TupleLiteral, rebinds: List[Stmt], seen: Dict[str, Expr],
                    ast_builder: 'ASTBuilder') -> List[DestructureTarget]:
    """A hidden binder per place, and the rebind of that place from its binder."""
    targets: List[DestructureTarget] = []
    for place in target.elements:
        if isinstance(place, TupleLiteral):
            targets.append(DestructureTarget(
                nested=_rebind_targets(place, rebinds, seen, ast_builder), loc=place.loc))
            continue
        spelled = _place_spelling(place)
        if spelled is not None:
            first = seen.get(spelled)
            if first is not None and first.loc is not None:
                ast_builder.recover(
                    SyntaxDiagnostic("CE6109", span=place.loc, place=spelled)
                    .note_at(f"'{spelled}' is first written here", first.loc)
                    .help("write each place once; a second assignment would replace the "
                          "first"), None)
            seen.setdefault(spelled, place)
        binder = hidden_name("rb", next(_destructure_ids))
        targets.append(DestructureTarget(name=binder, loc=place.loc, type_span=place.loc))
        rebinds.append(Rebind(target=place, value=Name(id=binder, loc=place.loc),
                              loc=place.loc))
    return targets


def _place_spelling(place: Expr) -> Optional[str]:
    """The source spelling of a place, or None when a part of it is not written plainly."""
    from sushi_lang.semantics.passes.borrow.diagnostics import expr_to_string
    from sushi_lang.semantics.places import Step, walk_place

    if walk_place(place, Step.MEMBER | Step.INDEX).name is None:
        return None
    spelled = expr_to_string(place)
    return None if "<expression>" in spelled else spelled
