"""Parser for first-class function types (fn_type_t)."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.typesys import FunctionType, UnknownType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_function_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse a function type (fn_type_t).

    Syntax: fn "(" [type_list] ")" "->" type ["|" type]
    Examples: fn(i32) -> i32, fn() -> ~, fn(i32, string) -> bool | MathError

    Tree children (anonymous string terminals are filtered out by Lark):
      [FN token, type_list?, return_type_tree, error_type_tree?]
    The optional error type defaults to UnknownType("StdError"), which the normal
    type-resolution pass binds to the StdError enum (mirroring fn declarations).
    """
    param_types = []
    direct_type_trees = []  # return type, then optional error type

    for child in node.children:
        if isinstance(child, Token):
            continue  # the FN keyword
        if isinstance(child, Tree) and child.data == "type_list":
            for type_node in child.children:
                param_type = ast_builder._parse_type(type_node)
                if param_type is None:
                    return None
                param_types.append(param_type)
        elif isinstance(child, Tree):
            direct_type_trees.append(child)

    if not direct_type_trees:
        # Return type is mandatory in the grammar; should not happen.
        return None

    ok_type = ast_builder._parse_type(direct_type_trees[0])
    if ok_type is None:
        return None

    if len(direct_type_trees) > 1:
        err_type = ast_builder._parse_type(direct_type_trees[1])
        if err_type is None:
            return None
    else:
        err_type = UnknownType("StdError")

    return FunctionType(
        param_types=tuple(param_types),
        ok_type=ok_type,
        err_type=err_type,
    )
