"""The two types a callable's signature declares: the return, and the `| E` channel.

A free function, an extension method and a perk contract method write the pair the same
way, and the `| E` channel is ONE language rule -- the contract and its implementation
must agree, or CE0133 refuses the pair. A rule with three readers can disagree with
itself before that check runs, so the read lives here and the three parsers call it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from sushi_lang.internals.report import Span, span_of
from sushi_lang.semantics.ast_builder.utils.tree_navigation import is_type_node
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


@dataclass(frozen=True, slots=True)
class SignatureTypes:
    """What a signature's type positions hold, with the span of each."""

    ret: Optional[Type] = None
    ret_span: Optional[Span] = None
    err: Optional[Type] = None
    err_span: Optional[Span] = None
    # Whether a return type was WRITTEN, which a parsed type cannot answer: the type
    # parser gives None for a malformed one too. Only `function_def` may omit the
    # return type; the two positions that make it mandatory refuse a missing one.
    declares_return: bool = False


def read_signature_types(children: List[object],
                         ast_builder: 'ASTBuilder') -> SignatureTypes:
    """Read the return type and the optional `| E` channel off a signature's children.

    The shape is `")" type ["|" type]`. With `maybe_placeholders=False` an omitted
    optional gives no child, so the return type is the FIRST direct type node and the
    channel is the SECOND. The caller hands in the children of the node that holds the
    signature, because an extension's TARGET type stands one level up and is not read
    here.
    """
    type_nodes = [child for child in children if is_type_node(child)]
    ret_node = type_nodes[0] if type_nodes else None
    err_node = type_nodes[1] if len(type_nodes) >= 2 else None

    return SignatureTypes(
        ret=ast_builder._parse_type(ret_node) if ret_node is not None else None,
        ret_span=span_of(ret_node),
        err=ast_builder._parse_type(err_node) if err_node is not None else None,
        err_span=span_of(err_node),
        declares_return=ret_node is not None,
    )
