"""Struct definition and field parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree
from sushi_lang.semantics.ast import StructDef, StructField, BoundedTypeParam
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_structdef(t: Tree, ast_builder: 'ASTBuilder') -> StructDef:
    """Parse struct_def: STRUCT NAME [type_params] ":" _NEWLINE _INDENT struct_field+ _DEDENT"""
    assert t.data == "struct_def"

    # Extract struct name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("struct_def: missing struct NAME")

    # Extract type parameters if present (e.g., <T> or <T: Hashable>)
    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    # Extract fields
    fields: List[StructField] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "struct_field":
            fields.append(parse_structfield(child, ast_builder))

    if not fields:
        raise NotImplementedError("struct_def: struct must have at least one field")

    return StructDef(
        name=str(name_tok),
        fields=fields,
        type_params=type_params,
        loc=span_of(t),
        name_span=span_of(name_tok),
    )


def parse_structfield(t: Tree, ast_builder: 'ASTBuilder') -> StructField:
    """Parse struct_field: type NAME _NEWLINE"""
    assert t.data == "struct_field"

    # Extract field type
    type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            type_node = child
            break

    # Extract field name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("struct_field: missing field NAME")

    # Parse type
    field_type = ast_builder._parse_type(type_node) if type_node else None

    return StructField(
        ty=field_type,
        name=str(name_tok),
        loc=span_of(t),
    )


def parse_bounded_type_params(type_params_node: Optional[Tree]) -> Optional[List[BoundedTypeParam]]:
    """Parse type_params node - delegates to generics module."""
    from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params as _parse_bounded
    return _parse_bounded(type_params_node)
