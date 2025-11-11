"""Parser for reference types."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree
from semantics.typesys import ReferenceType, Type, TYPE_NODE_NAMES

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_reference_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse reference type (reference_t).

    Syntax: "&" type
    Example: &i32, &string
    """
    # Find the referenced type (first child tree)
    referenced_type_node = None

    for child in node.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "array_t" or child.data == "dynamic_array_t"):
            referenced_type_node = child
            break

    if referenced_type_node is None:
        return None

    referenced_type = ast_builder._parse_type(referenced_type_node)
    if referenced_type is None:
        return None

    return ReferenceType(referenced_type=referenced_type)
