"""The two types a callable's signature declares: the return, and the `| E` channel.

A free function, an extension method and a perk contract method write the pair the same
way, and the `| E` channel is ONE language rule -- the contract and its implementation
must agree, or CE0133 refuses the pair. A rule with three readers can disagree with
itself before that check runs, so the read lives here and the three parsers call it.

The `dont_panic` marker stands after the pair, and its one reader lives here too.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import Span, span_of
from sushi_lang.semantics.ast_builder.utils.string_processing import strip_string_token
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_token, first_tree, ice, is_type_node)
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.unchecked_index import stamp_unchecked_indexes

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


@dataclass(frozen=True, slots=True)
class DontPanicMarker:
    """The `dont_panic because "<reason>"` marker of a header (docs/design/dont-panic.md)."""

    reason: str
    span: Optional[Span]


def read_dont_panic(children: List[object]) -> Optional[DontPanicMarker]:
    """Read the marker off a header's children; None when the header has none."""
    node = first_tree(children, "dont_panic")
    if node is None:
        return None
    reason_tok = first_token(node.children, "STRING")
    if reason_tok is None:
        ice(node, "missing the `because` STRING")
    return DontPanicMarker(reason=strip_string_token(reason_tok), span=span_of(node))


def mark_body(children: List[object],
              body: object) -> Tuple[Optional[str], Optional[Span]]:
    """Read the marker of a declaration with a body, and stamp the indexes of the body.

    The answer is the reason and the span that the declaration stores, or two Nones.
    """
    marker = read_dont_panic(children)
    if marker is None:
        return None, None
    stamp_unchecked_indexes(body)
    return marker.reason, marker.span


# The positions that parse the marker only to refuse it (CE6111, ruling D4): what the
# message names, why, and the help.
_REFUSED_POSITIONS = {
    "lambda": ("a lambda",
               "a lambda can outlive its function, so its indexes keep their check",
               "write the marker on a named function that holds the loop"),
    "perk_method": ("a perk contract method",
                    "a contract method has no body",
                    "write the marker on the implementation method"),
    "extern": ("an extern",
               "an extern has no body",
               "remove the marker; a C function has no index for Sushi to uncheck"),
}


def refuse_dont_panic(children: List[object], position: str,
                      ast_builder: 'ASTBuilder') -> None:
    """CE6111 when the header holds a marker in a position that takes none.

    The declaration is built as if the marker were not there, which is what the help asks.
    """
    marker = read_dont_panic(children)
    if marker is None:
        return
    named, reason, help_text = _REFUSED_POSITIONS[position]
    ast_builder.recover(
        SyntaxDiagnostic("CE6111", span=marker.span, position=named,
                         reason=reason).help(help_text),
        None)
