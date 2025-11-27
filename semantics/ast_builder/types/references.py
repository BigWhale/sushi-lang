"""Parser for reference types."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree, Token
from semantics.typesys import ReferenceType, BorrowMode, Type, TYPE_NODE_NAMES

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_reference_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse reference type (reference_t).

    Syntax: "&" ("peek" | "poke") type
    Example: &peek i32, &poke string
    """
    # Extract borrow mode (peek or poke)
    mutability = None
    referenced_type_node = None

    for child in node.children:
        if isinstance(child, Token) and child.type == "BORROW_MODE":
            mode_str = child.value.lower()
            if mode_str == "peek":
                mutability = BorrowMode.PEEK
            elif mode_str == "poke":
                mutability = BorrowMode.POKE
        elif isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "array_t" or child.data == "dynamic_array_t" or child.data == "reference_t"):
            referenced_type_node = child

    if mutability is None:
        # This should not happen with the new grammar
        return None

    if referenced_type_node is None:
        return None

    referenced_type = ast_builder._parse_type(referenced_type_node)
    if referenced_type is None:
        return None

    return ReferenceType(referenced_type=referenced_type, mutability=mutability)
