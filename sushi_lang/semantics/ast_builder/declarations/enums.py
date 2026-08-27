"""Enum definition and variant parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List
from lark import Tree
from sushi_lang.semantics.ast import EnumDef, EnumVariant
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    expect, first_name, first_tree, ice, read_public)
from sushi_lang.semantics.ast_builder.declarations.docs import attach_docs
from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params
from sushi_lang.internals.report import span_of
from sushi_lang.semantics.visibility import declared_public

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_enumdef(t: Tree, ast_builder: 'ASTBuilder') -> EnumDef:
    """Parse enum_def: ENUM NAME [type_params] ":" _NEWLINE _INDENT enum_variant+ _DEDENT"""
    t = expect(t, "enum_def")

    name_tok = first_name(t.children)
    if name_tok is None:
        ice(t, "missing enum NAME")

    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    variants: List[EnumVariant] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "enum_variant":
            variants.append(parse_enumvariant(child, ast_builder))

    if not variants:
        ice(t, "enum must have at least one variant")

    attach_docs(t.children, variants, ast_builder)

    marked, public_span = read_public(t.children)

    return EnumDef(
        name=str(name_tok),
        variants=variants,
        type_params=type_params,
        loc=span_of(t),
        name_span=span_of(name_tok),
        is_public=declared_public("enum", marked),
        public_span=public_span,
    )


def parse_enumvariant(t: Tree, ast_builder: 'ASTBuilder') -> EnumVariant:
    """Parse enum_variant: NAME ["(" enum_variant_fields ")"] _NEWLINE"""
    t = expect(t, "enum_variant")

    name_tok = first_name(t.children)
    if name_tok is None:
        ice(t, "missing variant NAME")

    associated_types: List[Type] = []
    fields_node = first_tree(t.children, "enum_variant_fields")
    if fields_node is not None:
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


