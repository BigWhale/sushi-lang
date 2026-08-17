"""Expression discovery utilities for structural disambiguation."""
from __future__ import annotations
from typing import List, Optional, Tuple
from lark import Tree, Token

from sushi_lang.semantics.ast_builder.utils.tree_navigation import ice


_EXPR_NODES = {
    "expr",        # Top-level expression wrapper
    "or_expr", "xor_expr", "and_expr",
    "bitwise_or", "bitwise_xor", "bitwise_and",
    "equality", "comparison",
    "shift",
    "add", "mul",
    "unary", "neg", "not", "bitnot",
    "maybe_call", "postfix", "atom",
}

_EXPR_PRIORITY = {
    "expr": 0,
    "or_expr": 1,
    "xor_expr": 2,
    "and_expr": 3,
    "bitwise_or": 4,
    "bitwise_xor": 5,
    "bitwise_and": 6,
    "equality": 7,
    "comparison": 8,
    "shift": 9,
    "cast": 10,
    "add": 11,
    "mul": 12,
    "unary": 13,
    "neg": 13,
    "not": 13,
    "bitnot": 13,
    "maybe_call": 14,
    "postfix": 14,
    "atom": 15,
}

_OP_TEXTS = {
    "+", "-", "*", "/", "%",
    "==", "!=", "<", "<=", ">", ">=",
    "and", "or", "xor", "&&", "||", "^^",
    "&", "|", "^", "<<", ">>", "~"
}

_OP_TYPES = {
    "AND", "OR", "XOR", "NOT", "BITNOT",
    "BIT_AND", "BIT_OR", "BIT_XOR",
    "LSHIFT", "RSHIFT",
}


def contains_expr_like(node: Tree) -> bool:
    """Check if a tree contains any expression-like node."""
    if node.data in _EXPR_NODES:
        return True
    for child in node.children:
        if isinstance(child, Tree) and contains_expr_like(child):
            return True
    return False


def contains_op(n: Tree) -> bool:
    """True if subtree 'n' contains any operator token."""
    stack: list[Tree] = [n]
    while stack:
        cur = stack.pop()
        for ch in cur.children:
            if isinstance(ch, Token):
                s = str(ch)
                if s in _OP_TEXTS or ch.type in _OP_TYPES:
                    return True
            elif isinstance(ch, Tree):
                stack.append(ch)
    return False


def token_count(n: Tree) -> int:
    """Count tokens under subtree; used to prefer the 'largest' expression."""
    cnt = 0
    stack: list[Tree] = [n]
    while stack:
        cur = stack.pop()
        for ch in cur.children:
            if isinstance(ch, Token):
                cnt += 1
            elif isinstance(ch, Tree):
                stack.append(ch)
    return cnt


def find_outer_expr_structural(container: Tree) -> Optional[Tree]:
    """Find the OUTERMOST expression subtree structurally, without relying on spans. Preference
    order: 1) Not nested inside another expr (i.e., outermost). 2) Contains an operator token
    (+, -, *, /, and/or, ==, <, ...). 3) Largest subtree by token count. 4) Higher-level kind by
    priority (expr > or_expr > ... > atom).
    """
    candidates: List[Tuple[Tree, List[Tree]]] = []

    def visit(n: Tree, ancestors: List[Tree]):
        if isinstance(n, Tree):
            if n.data in _EXPR_NODES:
                candidates.append((n, ancestors.copy()))
            for c in n.children:
                if isinstance(c, Tree):
                    visit(c, ancestors + [n])

    visit(container, [])
    if not candidates:
        return None

    outer: List[Tree] = []
    for node, anc in candidates:
        nested = any((other is not node) and (other in anc) for other, _ in candidates)
        if not nested:
            outer.append(node)
    if not outer:
        return candidates[0][0]

    with_ops = [n for n in outer if contains_op(n)]
    pool = with_ops if with_ops else outer

    pool.sort(key=lambda n: (-token_count(n), _EXPR_PRIORITY.get(n.data, 999)))
    return pool[0]


def expr_and_block(container: Tree) -> Tuple[Tree, Tree]:
    """Extract expression and block from if/while/foreach statement."""
    from sushi_lang.semantics.ast_builder.utils.tree_navigation import find_tree_recursive

    blk = find_tree_recursive(container, "block")
    if blk is None:
        ice(container, "clause missing block")

    expr = None
    for child in container.children:
        if isinstance(child, Tree) and child.data in _EXPR_NODES:
            if child != blk:
                expr = child
                break

    if expr is None:
        expr = find_outer_expr_structural(container)

    if expr is None:
        ice(container, "clause missing condition expr")
    return expr, blk
