"""Tree navigation utilities for traversing Lark parse trees."""
from __future__ import annotations
from typing import List, NoReturn, Optional, Callable
from lark import Tree, Token

from sushi_lang.internals.diagnostics import AstBuilderICE
from sushi_lang.internals.report import span_of


def _kind_of(node: object) -> str:
    """What the parse tree calls this node."""
    if isinstance(node, Tree):
        return str(node.data)
    if isinstance(node, Token):
        return str(node.type)
    return type(node).__name__


def ice(node: object, detail: str) -> NoReturn:
    """The grammar handed us a node whose children are not what we expect (CE0002).

    No source the grammar accepts can reach this, so it is a compiler bug -- but it
    is a *reported* one, with the offending node's span, not a traceback.
    """
    raise AstBuilderICE("CE0002", span=span_of(node), node=_kind_of(node), detail=detail)


def expect(t: object, *kinds: str) -> Tree:
    """Return `t` if it is a Tree of one of `kinds`; otherwise CE0003.

    Replaces the bare `assert t.data == "..."` guards, which vanish under python -O.
    """
    if not isinstance(t, Tree) or t.data not in kinds:
        unhandled(t)
    return t


def unhandled(node: object) -> NoReturn:
    """A node kind nothing dispatches on (CE0003) -- grammar/builder drift."""
    raise AstBuilderICE("CE0003", span=span_of(node), node=_kind_of(node))


def first(children: List[object], pred: Callable) -> Optional[object]:
    """Find first child matching predicate."""
    for ch in children:
        if pred(ch):
            return ch
    return None


def first_name(children: List[object]) -> Optional[Token]:
    """Get first NAME token from children."""
    return first(children, lambda c: isinstance(c, Token) and c.type == "NAME")  # type: ignore[return-value]


def first_method_name(children: List[object]) -> Optional[Token]:
    """Get first NAME or NEW token from children (for method names)."""
    return first(children, lambda c: isinstance(c, Token) and c.type in ("NAME", "NEW"))  # type: ignore[return-value]


def first_tree(children: List[object], data: str) -> Optional[Tree]:
    """Get first Tree child with specific data tag."""
    return first(children, lambda c: isinstance(c, Tree) and c.data == data)  # type: ignore[return-value]


def find_tree_recursive(n: Tree, data: str) -> Optional[Tree]:
    """Recursively find a tree node with specific data tag."""
    if n.data == data:
        return n
    for c in n.children:
        if isinstance(c, Tree):
            found = find_tree_recursive(c, data)
            if found:
                return found
    return None


def first_tree_child(t: Tree) -> Tree:
    """Get first Tree child, or report a malformed parse tree."""
    ch = next((c for c in t.children if isinstance(c, Tree)), None)
    if ch is None:
        ice(t, "missing operand")
    return ch
