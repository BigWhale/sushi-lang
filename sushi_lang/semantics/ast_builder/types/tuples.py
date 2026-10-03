"""The tuple list: one grammar rule for a tuple TYPE and a `let` destructure.

`tuple_list` takes `type NAME?` or `_` per element, because the parser cannot know which
of the two it reads until the token after the `)`. This module judges each element by its
POSITION (docs/design/tuples.md): a type takes types alone, a destructure takes binders.
"""
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from lark import Token, Tree

from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of
from sushi_lang.semantics.ast import DestructureTarget
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    expect, first_name, first_tree, ice, is_type_node)
from sushi_lang.semantics.generics.tuples import tuple_ref
from sushi_lang.semantics.typesys import ReferenceType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def _elements(tuple_list: Tree) -> List[Tree]:
    """The `tuple_elem` children of a `tuple_list`, in source order."""
    tuple_list = expect(tuple_list, "tuple_list")
    return [child for child in tuple_list.children
            if isinstance(child, Tree) and child.data == "tuple_elem"]


def _parts(elem: Tree) -> tuple[Optional[Tree], Optional[Token], Optional[Token]]:
    """The written type, the binder NAME and the `_` of one element."""
    type_node = next((c for c in elem.children if is_type_node(c)), None)
    name = first_name(elem.children)
    underscore = next((c for c in elem.children
                       if isinstance(c, Token) and c.type == "UNDERSCORE"), None)
    return type_node, name, underscore


def parse_tuple_type(node: Tree, ast_builder: 'ASTBuilder') -> Optional[Type]:
    """A tuple TYPE: every element is a type, and no element names a binding."""
    tuple_list = first_tree(node.children, "tuple_list")
    if tuple_list is None:
        ice(node, "tuple_t without a tuple_list")
    elements: List[Type] = []
    named = False
    for elem in _elements(tuple_list):
        type_node, name, underscore = _parts(elem)
        if type_node is None or underscore is not None:
            raise SyntaxDiagnostic(
                "CE6105", span=span_of(elem),
                reason="a tuple type names no binding, and `_` is not a type") \
                .help("write a type in each position, as in `(i32, string)`")
        element = ast_builder._parse_type(type_node)
        if element is None:
            ice(elem, "tuple element type did not parse")
        if isinstance(element, ReferenceType):
            element = ast_builder.recover(
                SyntaxDiagnostic("CE6107", span=span_of(type_node),
                                 mode=element.mutability.value)
                .help("a tuple holds values; write the element type alone"),
                element.referenced_type)
        if name is not None and not named:
            named = True
            ast_builder.recover(
                SyntaxDiagnostic(
                    "CE6105", span=span_of(name),
                    reason=f"a tuple type names no binding, so '{name}' has no place here")
                .help("a record with names is a struct; write the types alone, as in "
                      "`(i32, i32)`"),
                None)
        elements.append(element)
    return tuple_ref(elements)


def parse_destructure_targets(tuple_list: Tree,
                              ast_builder: 'ASTBuilder') -> List[DestructureTarget]:
    """The targets of a destructure: binders, `_`, and nested destructures."""
    return [_target(elem, ast_builder) for elem in _elements(tuple_list)]


def _target(elem: Tree, ast_builder: 'ASTBuilder') -> DestructureTarget:
    type_node, name, underscore = _parts(elem)
    loc = span_of(elem)
    if underscore is not None:
        return DestructureTarget(loc=loc)
    if type_node is None:
        ice(elem, "tuple element with neither a type nor `_`")
    if name is not None:
        if type_node.data == "reference_t":
            mode = next(str(c) for c in type_node.children
                        if isinstance(c, Token) and c.type == "BORROW_MODE")
            raise SyntaxDiagnostic("CE6107", span=span_of(type_node), mode=mode) \
                .help("a binder owns its element from an owned value and borrows it from "
                      "a borrow; remove the mode")
        return DestructureTarget(name=str(name), ty=ast_builder._parse_type(type_node),
                                 name_span=span_of(name), type_span=span_of(type_node),
                                 loc=loc)
    if type_node.data == "name_t":
        bare = first_name(type_node.children)
        if bare is not None and str(bare) != "ptr":
            return DestructureTarget(name=str(bare), name_span=span_of(bare), loc=loc)
    if type_node.data == "tuple_t":
        nested = first_tree(type_node.children, "tuple_list")
        if nested is not None:
            return DestructureTarget(nested=parse_destructure_targets(nested, ast_builder),
                                     loc=loc)
    if type_node.data == "reference_t":
        mode = next(str(c) for c in type_node.children
                    if isinstance(c, Token) and c.type == "BORROW_MODE")
        raise SyntaxDiagnostic("CE6107", span=span_of(type_node), mode=mode) \
            .help("a binder owns its element from an owned value and borrows it from a "
                  "borrow; remove the mode")
    raise SyntaxDiagnostic(
        "CE6105", span=loc,
        reason="a destructure element is a name, a typed name, `_` or a nested "
               "destructure") \
        .help("name the element, as in `i32 x`, or discard it with `_`")
