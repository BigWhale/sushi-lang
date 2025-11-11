"""Tree navigation utilities for traversing Lark parse trees."""
from __future__ import annotations
from typing import List, Optional, Callable
from lark import Tree, Token


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
    """Get first Tree child or raise if missing."""
    ch = next((c for c in t.children if isinstance(c, Tree)), None)
    if ch is None:
        raise NotImplementedError(f"missing operand under '{t.data}'")
    return ch
