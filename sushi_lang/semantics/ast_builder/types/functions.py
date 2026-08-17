"""Parser for first-class function types (fn_type_t)."""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.param_modes import ParamMode, normalize_modes
from sushi_lang.semantics.typesys import FunctionType, UnknownType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_function_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse a function type (fn_type_t)."""
    param_types = []
    nom_flags = []
    direct_type_trees = []  # return type, then optional error type

    for child in node.children:
        if isinstance(child, Token):
            continue  # the FN keyword
        if isinstance(child, Tree) and child.data == "fn_param_types":
            for param_node in child.children:
                nom_flags.append(any(isinstance(c, Token) and c.type == "NOM"
                                     for c in param_node.children))
                type_node = next((c for c in param_node.children
                                  if isinstance(c, Tree)), None)
                if type_node is None:
                    return None
                param_type = ast_builder._parse_type(type_node)
                if param_type is None:
                    return None
                param_types.append(param_type)
        elif isinstance(child, Tree):
            direct_type_trees.append(child)

    if not direct_type_trees:
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
        param_modes=normalize_modes(param_types, [
            ParamMode.NOM if flag else ParamMode.BORROW for flag in nom_flags
        ]),
    )
