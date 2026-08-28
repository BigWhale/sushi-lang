"""Parser for generic type instantiations."""
from __future__ import annotations
from typing import Optional, List, TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES, UnknownType
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_name, first_tree, name_tokens)
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_type_list(type_list_node: Tree, ast_builder: 'ASTBuilder') -> List[Type]:
    """Turn a `type_list` parse node into a list of resolved Types."""
    type_args: List[Type] = []
    for child in type_list_node.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t" or child.data == "generic_type_t"):
            arg_type = ast_builder._parse_type(child)
            if arg_type is not None:
                type_args.append(arg_type)
    return type_args


def parse_generic_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """Parse a generic type instantiation, bare or behind an alias.

    `qualified_generic_type_t` carries two NAMEs and `generic_type_t` one. The
    qualifier is recorded and the base name is the table key (Ruling 4).
    """
    names = name_tokens(node.children)
    if not names:
        return None

    namespace = str(names[0]) if len(names) > 1 else None
    base_name = str(names[-1])

    type_list_node = first_tree(node.children, "type_list")
    if type_list_node is None:
        return None

    type_args: List[Type] = parse_type_list(type_list_node, ast_builder)

    if not type_args:
        return None

    # Normalize single-arg `Result<T>` to `Result<T, StdError>`. Result carries an
    # implicit error type just like an `fn foo() T` declaration; supplying the default
    # StdError here lets every downstream two-arg Result path (resolution, propagation,
    # monomorphization) work unchanged. Mirrors FunctionType's implicit error slot.
    if base_name == "Result" and len(type_args) == 1:
        type_args.append(UnknownType("StdError"))

    return GenericTypeRef(base_name=base_name, type_args=tuple(type_args),
                          namespace=namespace)


def parse_bounded_type_params(type_params_node: Optional[Tree]) -> Optional[List[BoundedTypeParam]]:
    """Parse type_params node and extract bounded type parameters with constraints."""
    if type_params_node is None:
        return None

    param_list_node = first_tree(type_params_node.children, "type_param_list")
    if param_list_node is None:
        return None

    bounded_params: List[BoundedTypeParam] = []

    for child in param_list_node.children:
        if isinstance(child, Tree) and child.data == "type_param":
            # A type pack (`...Ts`) is prefixed with an ELLIPSIS token; the NAME is
            # then `children[1]`. A regular param has no prefix (NAME is first).
            is_pack = any(
                isinstance(c, Token) and c.type == "ELLIPSIS"
                for c in child.children
            )

            param_name = first_name(child.children)
            if param_name is None:
                continue

            perk_constraints_node = first_tree(child.children, "perk_constraints")
            constraints, namespaces = _parse_perk_constraints(perk_constraints_node)

            bounded_params.append(BoundedTypeParam(
                name=str(param_name),
                constraints=constraints,
                loc=span_of(child),
                is_pack=is_pack,
                constraint_namespaces=namespaces,
            ))
        elif isinstance(child, Token) and child.type == "NAME":
            bounded_params.append(BoundedTypeParam(
                name=str(child),
                constraints=[],
                loc=span_of(child)
            ))

    return bounded_params if bounded_params else None


def _parse_perk_constraints(
    perk_constraints_node: Optional[Tree],
) -> tuple[List[str], List[Optional[str]]]:
    """The perk names one type parameter is constrained by, and their qualifiers.

    Two index-aligned lists rather than one list of pairs: `constraints` is the table
    key every existing reader wants, and only the rule that checks a qualifier reaches
    for the second (`docs/design/unit-namespaces.md` section 5).
    """
    constraints: List[str] = []
    namespaces: List[Optional[str]] = []
    if perk_constraints_node is None:
        return constraints, namespaces

    constraint_list_node = first_tree(perk_constraints_node.children,
                                      "perk_constraint_list")
    if constraint_list_node is None:
        return constraints, namespaces

    for constraint_child in constraint_list_node.children:
        if not isinstance(constraint_child, Tree) or constraint_child.data != "perk_constraint":
            continue
        names = name_tokens(constraint_child.children)
        if not names:
            continue
        constraints.append(str(names[-1]))
        namespaces.append(str(names[0]) if len(names) > 1 else None)

    return constraints, namespaces
