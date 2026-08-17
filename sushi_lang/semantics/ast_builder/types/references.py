"""Parser for reference types."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.semantics.ast_builder.utils.tree_navigation import span_of
from sushi_lang.semantics.typesys import ReferenceType, BorrowMode, Type, TYPE_NODE_NAMES

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_reference_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse reference type (reference_t)."""
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
        return None

    if referenced_type_node is None:
        return None

    referenced_type = ast_builder._parse_type(referenced_type_node)
    if referenced_type is None:
        return None

    # `peek peek i32` PARSES, because the grammar rule is recursive, and it has no meaning
    # -- a borrow of a borrow is the same borrow (CE2418, #317). Rejected HERE, in the type
    # builder, because one site then covers every position a type can appear in.
    if isinstance(referenced_type, ReferenceType):
        raise SyntaxDiagnostic(
            "CE2418", span=span_of(node),
            outer=mutability.value, inner=referenced_type.mutability.value,
        ).help("write the single borrow: a borrow of a borrow is the same borrow")

    return ReferenceType(referenced_type=referenced_type, mutability=mutability)
