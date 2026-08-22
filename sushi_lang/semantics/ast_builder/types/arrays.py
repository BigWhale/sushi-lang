"""Parser for array types (fixed and dynamic)."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, TYPE_NODE_NAMES

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_array_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[ArrayType]:
    """Parse fixed-size array type (array_t)."""
    base_type_node = None
    size_token = None

    for child in node.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "reference_t"):
            base_type_node = child
        elif isinstance(child, Token) and child.type == "INT":
            size_token = child

    if base_type_node is None or size_token is None:
        return None

    base_type = ast_builder._parse_type(base_type_node)
    if base_type is None:
        return None

    # An array size is the second consumer of an INT token, so it normalizes
    # through the same seam. Reading `int(size_token.value)` directly would let
    # Python's own underscore rules decide -- and they are looser than ours, since
    # `int()` accepts `0x_FF`. A malformed underscore must be CE6006 here too,
    # not a silent None that surfaces as a failure to parse the type.
    from sushi_lang.semantics.ast_builder.expressions.literals import normalize_numeric

    try:
        size = int(normalize_numeric(size_token))
        if size <= 0:
            return None  # Invalid array size
        return ArrayType(base_type=base_type, size=size)
    except ValueError:
        return None


def parse_dynamic_array_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[DynamicArrayType]:
    """Parse dynamic array type (dynamic_array_t)."""
    base_type_node = None

    for child in node.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "reference_t"):
            base_type_node = child
            break

    if base_type_node is None:
        return None

    base_type = ast_builder._parse_type(base_type_node)
    if base_type is None:
        return None

    return DynamicArrayType(base_type=base_type)
