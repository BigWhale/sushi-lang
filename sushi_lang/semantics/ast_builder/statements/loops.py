"""Loop statement parsing (foreach)."""
from __future__ import annotations
from itertools import count
from typing import TYPE_CHECKING, Optional, Sequence, Union
from lark import Tree, Token
from sushi_lang.semantics.ast import Block, Expr, Foreach, Expand, Let, Name, TryExpr
from sushi_lang.semantics.typesys import ReferenceType, Type
from sushi_lang.semantics.ast_builder.utils.tree_navigation import ice, is_type_node
from sushi_lang.internals.report import span_of, Span

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder

# One counter for the whole process. A `??` binder needs a name for the loop's own
# binding that the user cannot have written, and a nested loop must not shadow its
# parent's -- `__` is not a legal identifier start in Sushi, so nothing can collide.
_try_binder_ids = count()

_Child = Union[Tree, Token]


def _desugar_try_binder(item_name: str, item_name_span: Optional[Span],
                        try_token: Token, body: Block) -> tuple[str, Let]:
    """Turn `foreach(x?? in it)` into a hidden binding plus `let <T> x = <hidden>??`.

    The `??` is a MARKER, not an operator, and this is the whole of its implementation:
    the loop binds the raw item under a hidden name and the body opens with an ordinary
    `Let` of an ordinary `TryExpr`. So the unwrap, the error propagation, the channel
    check (CE2511) and the scope cleanup on the error path are all the ones `??` already
    has everywhere else -- there is no second implementation to keep in step.

    The `Let` leaves here with no type. The typecheck pass fills it in from the item
    type, which is the one thing the parser cannot know.
    """
    hidden = f"__fe_item{next(_try_binder_ids)}"
    unwrap = Let(
        name=item_name,
        ty=None,
        value=TryExpr(expr=Name(id=hidden, loc=item_name_span), loc=span_of(try_token)),
        name_span=item_name_span,
        type_span=item_name_span,
        loc=span_of(try_token),
    )
    body.statements.insert(0, unwrap)
    return hidden, unwrap


def _loop_tail(node: Tree, ast_builder: 'ASTBuilder') -> tuple[Expr, Block]:
    """Read the iterable and the body off the END of a loop rule.

    Every loop rule closes with `"in" expr ")" ":" block`, and the parser drops an
    anonymous literal, so the last two children are the iterable and the block in all
    three rules. The read is by rule POSITION and needs no cursor: an optional in
    front of the tail cannot move it, and an index cursor that stays put is what
    turned a missed predicate into a crash here before (#595).
    """
    children = node.children
    if len(children) < 2:
        ice(node, f"{node.data} expects an iterable and a block, "
                  f"got {len(children)} children")
    return ast_builder._expr(children[-2]), ast_builder._block(children[-1])


def _token_at(node: Tree, children: Sequence[_Child], index: int,
              kind: str, where: str) -> Token:
    """Read one named terminal at a position the grammar fixes, or stop.

    A read that finds something else is drift between the grammar and this builder.
    It stops here with a located ICE. It must never continue, and it must never be
    silent.
    """
    found = children[index] if -len(children) <= index < len(children) else None
    if not isinstance(found, Token) or found.type != kind:
        ice(node, f"{node.data} expects {kind} {where}, got {found}")
    return found


def parse_foreach_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Foreach:
    """Parse foreach_stmt: FOREACH "(" [type] NAME [DOUBLE_QUESTION] "in" expr ")" ":" block"""
    children = node.children
    _token_at(node, children, 0, "FOREACH", "first")

    # Between the keyword and the tail stand the optional item type, the item name and
    # the optional `??` marker. The parser drops an optional it did not see, so this
    # slice holds one, two or three nodes. Peel it from the back, where the order is
    # fixed: the marker, then the name, then whatever is left is the type.
    head: Sequence[_Child] = children[1:-2]

    try_token: Optional[Token] = None
    if head and isinstance(head[-1], Token) and head[-1].type == "DOUBLE_QUESTION":
        try_token = head[-1]
        head = head[:-1]

    name_tok = _token_at(node, head, -1, "NAME", "for the item name")
    item_name = name_tok.value
    item_name_span = span_of(name_tok)

    item_type: Optional[Type] = None
    item_type_span: Optional[Span] = None
    written_type = head[:-1]
    if written_type:
        if len(written_type) != 1 or not is_type_node(written_type[0]):
            ice(node, f"foreach_stmt expects at most one type before the item "
                      f"name, got {list(written_type)}")
        item_type = ast_builder._parse_type(written_type[0])
        item_type_span = span_of(written_type[0])

    iterable, body = _loop_tail(node, ast_builder)

    item_try_let: Optional[Let] = None
    item_try_span: Optional[Span] = None
    if try_token is not None:
        item_try_span = span_of(try_token)
        item_name, item_try_let = _desugar_try_binder(
            item_name, item_name_span, try_token, body)

    # A reference-typed item (`foreach(poke i32 r in ...)`) is the long spelling of
    # the marker form (`foreach(poke r in ...)`) -- normalize it, so every downstream
    # pass sees ONE spelling: `item_borrow` set, `item_type` the referent (#300).
    item_borrow: Optional[str] = None
    item_borrow_span: Optional[Span] = None
    if isinstance(item_type, ReferenceType):
        item_borrow = item_type.mutability.value
        item_borrow_span = item_type_span
        item_type = item_type.referenced_type

    return Foreach(
        item_name=item_name,
        item_type=item_type,
        iterable=iterable,
        body=body,
        item_name_span=item_name_span,
        item_type_span=item_type_span,
        item_borrow=item_borrow,
        item_borrow_span=item_borrow_span,
        item_try_let=item_try_let,
        item_try_span=item_try_span,
        loc=span_of(node)
    )


def parse_foreach_ref(node: Tree, ast_builder: 'ASTBuilder') -> Foreach:
    """Parse foreach_ref: FOREACH "(" BORROW_MODE NAME "in" expr ")" ":" block"""
    children = node.children
    _token_at(node, children, 0, "FOREACH", "first")
    mode_tok = _token_at(node, children, 1, "BORROW_MODE", "for the binding mode")
    name_tok = _token_at(node, children, 2, "NAME", "for the item name")
    iterable, body = _loop_tail(node, ast_builder)

    return Foreach(
        item_name=name_tok.value,
        item_type=None,
        iterable=iterable,
        body=body,
        item_name_span=span_of(name_tok),
        item_type_span=None,
        item_borrow=mode_tok.value,
        item_borrow_span=span_of(mode_tok),
        loc=span_of(node)
    )


def parse_expand_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Expand:
    """Parse expand_stmt: EXPAND "(" NAME "in" expr ")" ":" block"""
    children = node.children
    _token_at(node, children, 0, "EXPAND", "first")
    name_tok = _token_at(node, children, 1, "NAME", "for the pack name")
    iterable, body = _loop_tail(node, ast_builder)

    return Expand(
        var=name_tok.value,
        iterable=iterable,
        body=body,
        var_span=span_of(name_tok),
        loc=span_of(node),
    )
