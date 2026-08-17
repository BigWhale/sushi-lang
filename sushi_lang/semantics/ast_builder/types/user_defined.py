"""Parser for user-defined types (structs, enums)."""
from __future__ import annotations
from lark import Tree
from sushi_lang.semantics.typesys import UnknownType, ForeignPtrType
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name


def parse_unknown_type(node: Tree):
    """Parse a bare type name (name_t)."""
    name_token = first_name(node.children)
    if name_token:
        name = str(name_token)
        if name == "ptr":
            return ForeignPtrType()
        return UnknownType(name=name)
    return None
