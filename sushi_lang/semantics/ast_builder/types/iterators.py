"""The written `Iterator@(...)`, bare or behind an alias, with its item mode.

`peek` is a legal type argument only here: at the top of the item, or at the top of one
element of a tuple item (docs/design/iteration.md). A `peek` deeper in the item meets the
rule of the position that holds it (CE2419 in a generic, CE6107 in a tuple).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from lark import Tree

from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of
from sushi_lang.semantics.ast_builder.types.tuples import parse_tuple_type
from sushi_lang.semantics.ast_builder.utils.tree_navigation import ice, name_tokens
from sushi_lang.semantics.generics.iterators import ITERATOR_BASE, iterator_of
from sushi_lang.semantics.generics.tuples import is_tuple_type
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import IteratorType, ReferenceType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def is_iterator_node(node: Tree) -> bool:
    """Does this `generic_type_t` / `qualified_generic_type_t` write `Iterator@(...)`?"""
    names = name_tokens(node.children)
    return bool(names) and str(names[-1]) == ITERATOR_BASE


def parse_iterator_type(node: Tree, args: List[Tree],
                        ast_builder: 'ASTBuilder') -> IteratorType:
    """`Iterator@(T)`, `Iterator@(peek T)` or `Iterator@((i32, peek string))`.

    `args` is each element of the type list, as the generic reader finds them.
    """
    names = name_tokens(node.children)
    if len(args) != 1:
        raise SyntaxDiagnostic("CE2062", span=span_of(node), name=ITERATOR_BASE,
                               expected="1", got=len(args))
    namespace = str(names[0]) if len(names) > 1 else None
    return iterator_of(_read_item(args[0], ast_builder), base_name=ITERATOR_BASE,
                       namespace=namespace)


def _read_item(node: Tree, ast_builder: 'ASTBuilder') -> Type:
    """The item as written: a `peek` stays on it, or on an element of a tuple item."""
    if node.data == "tuple_t":
        item = parse_tuple_type(node, ast_builder, lambda element, written:
                                _refuse_poke(element, written, ast_builder))
    else:
        item = ast_builder._parse_type(node)
    if item is None:
        ice(node, "an iterator item read as no type")
    if not isinstance(item, ReferenceType):
        return item
    if is_tuple_type(item.referenced_type):
        return ast_builder.recover(
            SyntaxDiagnostic("CE2419", span=span_of(node), ty=display_type(item))
            .help("a tuple item takes `peek` on an element, as in "
                  "`Iterator@((i32, peek string))`"),
            item.referenced_type)
    return _refuse_poke(item, node, ast_builder)


def _refuse_poke(element: ReferenceType, written: Tree, ast_builder: 'ASTBuilder') -> Type:
    """A `poke` item is not an item mode: an item is owned or `peek`."""
    if element.is_peek():
        return element
    return ast_builder.recover(
        SyntaxDiagnostic("CE2419", span=span_of(written), ty=display_type(element))
        .help("an iterator item is owned or `peek`; there is no `poke` item"),
        element.referenced_type)
