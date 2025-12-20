"""Parser for generic type instantiations."""
from __future__ import annotations
from typing import Optional, List, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_generic_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse generic type instantiation (generic_type_t).

    Syntax: NAME "<" type_list ">"
    Examples: Result<i32>, Maybe<string>
    """
    # Extract base name
    name_token = first_name(node.children)
    if name_token is None:
        return None

    base_name = str(name_token)

    # Extract type arguments from type_list
    type_list_node = first_tree(node.children, "type_list")
    if type_list_node is None:
        return None

    type_args: List[Type] = []
    for child in type_list_node.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "generic_type_t"):
            arg_type = ast_builder._parse_type(child)
            if arg_type is not None:
                type_args.append(arg_type)

    if not type_args:
        return None

    return GenericTypeRef(base_name=base_name, type_args=tuple(type_args))


def parse_bounded_type_params(type_params_node: Optional[Tree]) -> Optional[List[BoundedTypeParam]]:
    """Parse type_params node and extract bounded type parameters with constraints.

    Grammar: type_params: "<" type_param_list ">"
             type_param_list: type_param ("," type_param)*
             type_param: NAME [perk_constraints]
             perk_constraints: ":" perk_constraint_list
             perk_constraint_list: NAME ("+" NAME)*
    """
    if type_params_node is None:
        return None

    param_list_node = first_tree(type_params_node.children, "type_param_list")
    if param_list_node is None:
        return None

    bounded_params: List[BoundedTypeParam] = []

    for child in param_list_node.children:
        if isinstance(child, Tree) and child.data == "type_param":
            # Extract the parameter name
            param_name = first_name(child.children)
            if param_name is None:
                continue

            # Extract constraints if present
            constraints: List[str] = []
            perk_constraints_node = first_tree(child.children, "perk_constraints")
            if perk_constraints_node is not None:
                constraint_list_node = first_tree(perk_constraints_node.children, "perk_constraint_list")
                if constraint_list_node is not None:
                    # Extract all NAME tokens from constraint list
                    for constraint_child in constraint_list_node.children:
                        if isinstance(constraint_child, Token) and constraint_child.type == "NAME":
                            constraints.append(str(constraint_child))

            bounded_params.append(BoundedTypeParam(
                name=str(param_name),
                constraints=constraints if constraints else [],
                loc=span_of(child)
            ))
        elif isinstance(child, Token) and child.type == "NAME":
            # Backwards compatibility: direct NAME tokens without constraints
            bounded_params.append(BoundedTypeParam(
                name=str(child),
                constraints=[],
                loc=span_of(child)
            ))

    return bounded_params if bounded_params else None
