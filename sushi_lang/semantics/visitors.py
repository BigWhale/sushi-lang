"""AST Visitor Pattern implementation for the Sushi language compiler."""
from __future__ import annotations
from abc import ABC
from typing import TypeVar, Generic

from sushi_lang.semantics.ast import Node

T = TypeVar("T")


class NodeVisitor(ABC, Generic[T]):
    """Abstract base class for AST node visitors."""

    def visit(self, node: Node) -> T:
        """Main entry point for visiting a node."""
        method_name = f'visit_{type(node).__name__.lower()}'
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node: Node) -> T:
        """Default visitor that raises an error. Subclasses should either implement specific
        visit_* methods or override this to provide default behavior.
        """
        raise NotImplementedError(
            f"Visitor {self.__class__.__name__} doesn't handle {type(node).__name__}"
        )
