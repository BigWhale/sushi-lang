"""Parser for user-defined types (structs, enums)."""
from __future__ import annotations
from typing import Optional
from lark import Tree
from sushi_lang.semantics.typesys import UnknownType
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name


def parse_unknown_type(node: Tree) -> Optional[UnknownType]:
    """Parse user-defined type name (name_t).

    Syntax: NAME

    Returns UnknownType that will be resolved later to StructType or EnumType.
    """
    name_token = first_name(node.children)
    if name_token:
        return UnknownType(name=str(name_token))
    return None
