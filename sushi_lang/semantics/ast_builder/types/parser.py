"""Main type parser coordinating specialized type parsers."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.typesys import Type, type_from_rule_name
from sushi_lang.semantics.ast_builder.types import user_defined, generics, arrays, references, functions

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


class TypeParser:
    """Coordinates type parsing across specialized parsers."""

    def __init__(self, ast_builder: 'ASTBuilder'):
        """Initialize TypeParser with reference to ASTBuilder for recursive parsing."""
        self.ast_builder = ast_builder

    def parse_type(self, type_node: Tree) -> Optional[Type]:
        """Parse a type node into a Type object, handling all type syntax."""
        tag = type_node.data

        if tag == "name_t":
            return user_defined.parse_unknown_type(type_node)
        elif tag == "qualified_name_t":
            return user_defined.parse_qualified_type(type_node)
        elif tag in ("generic_type_t", "qualified_generic_type_t"):
            return generics.parse_generic_type(type_node, self.ast_builder)
        elif tag == "array_t":
            return arrays.parse_array_type(type_node, self.ast_builder)
        elif tag == "dynamic_array_t":
            return arrays.parse_dynamic_array_type(type_node, self.ast_builder)
        elif tag == "reference_t":
            return references.parse_reference_type(type_node, self.ast_builder)
        elif tag == "fn_type_t":
            return functions.parse_function_type(type_node, self.ast_builder)
        else:
            return type_from_rule_name(tag)
