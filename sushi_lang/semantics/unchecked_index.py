"""The stamp of a `dont_panic` body: its `[]` emit no bounds check.

The AST builder stamps each `IndexAccess` of a marked body, and the backend reads the
stamp. The walk stops at a lambda (docs/design/dont-panic.md, D5): the lift pass makes a
lambda a function of its own, which can run after the marked function returned, so the
indexes in a lambda body keep their check.
"""
from __future__ import annotations

from sushi_lang.semantics.ast import IndexAccess, Lambda, Node
from sushi_lang.semantics.ast_walk import walk_nodes


def stamp_unchecked_indexes(body: object) -> None:
    """Set `unchecked` on every index of `body` that is not in a lambda."""

    def stamp(node: Node) -> bool:
        if isinstance(node, Lambda):
            return False
        if isinstance(node, IndexAccess):
            node.unchecked = True
        return True

    walk_nodes(body, stamp)


def holds_an_unchecked_index(body: object) -> bool:
    """Does `body` hold an index with the stamp? An index in a lambda does not count."""
    found = False

    def look(node: Node) -> bool:
        nonlocal found
        if found or isinstance(node, Lambda):
            return False
        if isinstance(node, IndexAccess) and node.unchecked:
            found = True
        return not found

    walk_nodes(body, look)
    return found
