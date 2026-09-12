"""Tree navigation utilities for traversing Lark parse trees."""
from __future__ import annotations
from typing import List, NoReturn, Optional, Callable
from lark import Tree, Token

from sushi_lang.internals.diagnostics import AstBuilderICE
from sushi_lang.internals.report import Span, span_of
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES


def _kind_of(node: object) -> str:
    """What the parse tree calls this node."""
    if isinstance(node, Tree):
        return str(node.data)
    if isinstance(node, Token):
        return str(node.type)
    return type(node).__name__


def ice(node: object, detail: str) -> NoReturn:
    """The grammar handed us a node whose children are not what we expect (CE0002)."""
    raise AstBuilderICE("CE0002", span=span_of(node), node=_kind_of(node), detail=detail)


def expect(t: object, *kinds: str) -> Tree:
    """Return `t` if it is a Tree of one of `kinds`; otherwise CE0003."""
    if not isinstance(t, Tree) or t.data not in kinds:
        unhandled(t)
    return t


def unhandled(node: object) -> NoReturn:
    """A node kind nothing dispatches on (CE0003) -- grammar/builder drift."""
    raise AstBuilderICE("CE0003", span=span_of(node), node=_kind_of(node))


def first(children: List[object], pred: Callable) -> Optional[object]:
    """Find first child matching predicate."""
    for ch in children:
        if pred(ch):
            return ch
    return None


def first_name(children: List[object]) -> Optional[Token]:
    """Get first NAME token from children."""
    return first(children, lambda c: isinstance(c, Token) and c.type == "NAME")  # type: ignore[return-value]


def first_token(children: List[object], token_type: str) -> Optional[Token]:
    """Get the first token of `token_type` among the children."""
    return first(children, lambda c: isinstance(c, Token) and c.type == token_type)  # type: ignore[return-value]


def name_tokens(children: List[object]) -> List[Token]:
    """Every NAME token among the children, in source order.

    A qualified name is two of them and a bare one is a single one, so the reader that
    splits `geo.Vec` into a namespace and a name asks for the list rather than for the
    first (`docs/design/unit-namespaces.md` section 5).
    """
    return [c for c in children if isinstance(c, Token) and c.type == "NAME"]


# The tokens the grammar's `method_name` admits. A keyword after a DOT is a method name
# and never a statement, so the set grows with the grammar rule and nowhere else.
_METHOD_NAME_TOKENS = ("NAME", "NEW", "EXTEND")


def first_method_name(children: List[object]) -> Optional[Token]:
    """Get the first method-name token from children."""
    return first(children, lambda c: isinstance(c, Token) and c.type in _METHOD_NAME_TOKENS)  # type: ignore[return-value]


def read_public(children: List[object]) -> tuple[bool, Optional[Span]]:
    """Is the `public` marker written here, and where?

    One reader for every rule that carries `PUBLIC?`. The span is what a diagnostic
    points at when the marker is in the wrong place (CE6103), and it is also the only
    way to tell a written marker from a kind whose unmarked default is still public.
    """
    token = first(children, lambda c: isinstance(c, Token) and c.type == "PUBLIC")
    if token is None:
        return False, None
    return True, span_of(token)


def is_type_node(node: object) -> bool:
    """Is this child a written type?

    One predicate for the question, because a reader that spells the membership test
    again can forget a member of `TYPE_NODE_NAMES` -- and then one position in the
    grammar refuses a type every other position takes (#595).
    """
    return isinstance(node, Tree) and node.data in TYPE_NODE_NAMES


def first_tree(children: List[object], data: str) -> Optional[Tree]:
    """Get first Tree child with specific data tag."""
    return first(children, lambda c: isinstance(c, Tree) and c.data == data)  # type: ignore[return-value]


def read_method_name(node: Tree) -> str:
    """The name a `method_call` or a `member_access` writes after the dot.

    One reader, because `method_name` carries a language rule: a keyword after a DOT
    is a method name, so `List.new()` and `a.extend(b)` are calls and not statements.
    A second reader lets that rule hold in one position and not the other.

    The grammar fixes the shape -- `method_name` has no `?`, so Lark always builds
    the Tree and it always holds one token. There is thus no fallback to the node's
    own children: a miss is grammar/builder drift and it stops here (CE0002).
    """
    name_tree = first_tree(node.children, "method_name")
    if name_tree is None:
        ice(node, "missing method_name")
    token = first_method_name(name_tree.children)
    if token is None:
        ice(name_tree, "method_name holds no name token")
    return str(token)


def find_tree_recursive(n: Tree, data: str) -> Optional[Tree]:
    """Recursively find a tree node with specific data tag."""
    if n.data == data:
        return n
    for c in n.children:
        if isinstance(c, Tree):
            found = find_tree_recursive(c, data)
            if found:
                return found
    return None


def first_tree_child(t: Tree) -> Tree:
    """Get first Tree child, or report a malformed parse tree."""
    ch = next((c for c in t.children if isinstance(c, Tree)), None)
    if ch is None:
        ice(t, "missing operand")
    return ch


def mark_nom(expr, token):
    """Stamp the `nom` marker a call argument, a `let` or a `return` may carry.

    ONE place, because `nom` is a call-site MARKER and not an operator: it sets a flag
    rather than wrapping the expression in a node every pass would then dispatch on.
    Three positions admit it -- `f(nom x)`, `let T x = nom s.f` and `return nom s.f` --
    and a fourth spelling of the same two lines is how one of them would drift.
    """
    expr.nom_marked = True
    expr.nom_span = span_of(token)
    return expr
