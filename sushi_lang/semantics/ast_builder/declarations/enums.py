"""Enum definition and variant parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree
from sushi_lang.semantics.ast import EnumDef, EnumVariant, BoundedTypeParam
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_enumdef(t: Tree, ast_builder: 'ASTBuilder') -> EnumDef:
    """Parse enum_def: ENUM NAME [type_params] ":" _NEWLINE _INDENT enum_variant+ _DEDENT"""
    assert t.data == "enum_def"

    # Extract enum name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("enum_def: missing enum NAME")

    # Extract type parameters if present (e.g., <T> or <T: Hashable>)
    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    # Extract variants
    variants: List[EnumVariant] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "enum_variant":
            variants.append(parse_enumvariant(child, ast_builder))

    if not variants:
        raise NotImplementedError("enum_def: enum must have at least one variant")

    return EnumDef(
        name=str(name_tok),
        variants=variants,
        type_params=type_params,
        loc=span_of(t),
        name_span=span_of(name_tok),
    )


def parse_enumvariant(t: Tree, ast_builder: 'ASTBuilder') -> EnumVariant:
    """Parse enum_variant: NAME ["(" enum_variant_fields ")"] _NEWLINE"""
    assert t.data == "enum_variant"

    # Extract variant name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("enum_variant: missing variant NAME")

    # Extract associated types (if any)
    associated_types: List[Type] = []
    fields_node = first_tree(t.children, "enum_variant_fields")
    if fields_node is not None:
        # Parse each type in the fields list
        for child in fields_node.children:
            if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
                ty = ast_builder._parse_type(child)
                if ty is not None:
                    associated_types.append(ty)

    return EnumVariant(
        name=str(name_tok),
        associated_types=associated_types,
        name_span=span_of(name_tok),
        loc=span_of(t),
    )


def parse_bounded_type_params(type_params_node: Optional[Tree]) -> Optional[List[BoundedTypeParam]]:
    """Parse type_params node - delegates to generics module."""
    from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params as _parse_bounded
    return _parse_bounded(type_params_node)
