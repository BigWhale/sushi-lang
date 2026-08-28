"""Parser for user-defined types (structs, enums)."""
from __future__ import annotations
from lark import Tree
from sushi_lang.semantics.typesys import UnknownType, ForeignPtrType
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, name_tokens


def parse_unknown_type(node: Tree):
    """Parse a bare type name (name_t)."""
    name_token = first_name(node.children)
    if name_token:
        name = str(name_token)
        if name == "ptr":
            return ForeignPtrType()
        return UnknownType(name=name)
    return None


def parse_qualified_type(node: Tree):
    """Parse a type name written behind an alias (qualified_name_t).

    The qualifier is recorded and the name is the table key, which is the whole of
    Ruling 4: `geo.Vec` NAMES the type `Vec` and does not create a second one
    (`docs/design/unit-namespaces.md` sections 5 and 7).
    """
    names = name_tokens(node.children)
    if len(names) != 2:
        return None
    return UnknownType(name=str(names[1]), namespace=str(names[0]))
