"""Member access and index access parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import Expr, MemberAccess, IndexAccess
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.semantics.ast_builder.utils.tree_navigation import read_method_name, ice, expect
from sushi_lang.internals.report import Span, span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def member_access_from_parts(receiver: Expr, member_access_node: Tree,
                             loc: Span | None) -> MemberAccess:
    """Parse member_access: \".\" method_name. `loc` spans the chain through the member."""
    member_access_node = expect(member_access_node, "member_access")

    return MemberAccess(
        receiver=receiver,
        member=str(read_method_name(member_access_node)),
        loc=loc,
        member_span=span_of(member_access_node),
    )


def index_access_from_parts(array_expr: Expr, index_node: Tree, ast_builder: 'ASTBuilder',
                            loc: Span | None) -> IndexAccess:
    """Parse index: \"[\" expr \"]\". `loc` spans the chain through the closing bracket."""
    index_node = expect(index_node, "index")

    index_expr_node = None
    for child in index_node.children:
        if isinstance(child, Tree):
            index_expr_node = child
            break

    if index_expr_node is None:
        ice(index_node, "missing index expression")

    return IndexAccess(
        array=array_expr,
        index=ast_builder._expr(index_expr_node),
        loc=loc,
    )


def _element_index(text: str, node: Tree) -> str:
    """One plain decimal index, `0` or a digit run with no leading zero, or CE6106."""
    if text.isdigit() and (text == "0" or not text.startswith("0")):
        return text
    reason = ("an index has no underscore" if "_" in text
              else "an index has no exponent" if text.lower().count("e")
              else "an index has no leading zero")
    raise SyntaxDiagnostic("CE6106", span=span_of(node), index=text, reason=reason) \
        .help("write the element number in decimal, as in `t.0` or `t.0.1`")


def tuple_index_from_parts(receiver: Expr, index_node: Tree,
                           loc: Span | None) -> MemberAccess:
    """`t.0` and `t.0.1`: a tuple element is the field named by its index.

    The lexer reads `t.0.1` as `t` `.` and the number `0.1`, so a FLOAT after the dot is
    split into two steps here (docs/design/tuples.md).
    """
    index_node = expect(index_node, "tuple_index", "tuple_index_pair")
    token = index_node.children[0]
    text = str(token)
    steps = text.split(".") if index_node.data == "tuple_index_pair" else [text]
    if len(steps) != len([step for step in steps if step]) or len(steps) > 2:
        raise SyntaxDiagnostic("CE6106", span=span_of(index_node), index=text,
                               reason="an index is a plain decimal number") \
            .help("write the element number in decimal, as in `t.0` or `t.0.1`")
    access = MemberAccess(receiver=receiver, member=_element_index(steps[0], index_node),
                          loc=loc, member_span=span_of(index_node))
    for step in steps[1:]:
        access = MemberAccess(receiver=access, member=_element_index(step, index_node),
                              loc=loc, member_span=span_of(index_node))
    return access
