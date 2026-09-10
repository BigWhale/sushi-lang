"""`EXPR_NODES` names every parse-tree node an expression can be, and nothing else.

The AST builder asks one question in seven places: is this direct child of a statement
the expression? Two hand-written lists used to answer it, and they disagreed -- one held
`cast`, the other did not, and neither held `range` or `borrow`. The lists were correct
only by accident, because `or_expr` carried no `?` and so stood outermost over every
expression the grammar could build.

This gate reads the answer out of the grammar. The parser knows which rules inline
(`?`) and which alias, so the set of node names that can stand at the root of an
expression is a closure over its own rules. A rule that grows, gets a `?`, or loses one
moves the closure, and this test says so before a call site fails in silence.
"""
from __future__ import annotations

from collections import defaultdict

from lark.grammar import NonTerminal

from sushi_lang.internals.parser import build_parser
from sushi_lang.semantics.ast_builder.utils.expression_discovery import EXPR_NODES
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES


def _root_kinds(start: str) -> tuple[set[str], set[str]]:
    """The Tree names and the Token names a `start` subtree can present at its root.

    Lark keeps a rule as a Tree unless the rule is marked `?` and exactly one child
    survives the filter, and an alias always wins over the `?`. So the walk follows a
    rule only where it inlines, and records the name everywhere else.
    """
    parser = build_parser()
    by_origin: dict[str, list] = defaultdict(list)
    for rule in parser.rules:
        by_origin[str(rule.origin.name)].append(rule)

    trees: set[str] = set()
    tokens: set[str] = set()
    seen: set[str] = set()
    pending = [start]

    while pending:
        origin = pending.pop()
        if origin in seen:
            continue
        seen.add(origin)
        for rule in by_origin[origin]:
            if rule.alias:
                trees.add(str(rule.alias))
                continue
            kept = [s for s in rule.expansion if not getattr(s, "filter_out", False)]
            if rule.options.expand1 and len(kept) == 1:
                only = kept[0]
                if isinstance(only, NonTerminal):
                    pending.append(str(only.name))
                else:
                    tokens.add(str(only.name))
                continue
            trees.add(origin)

    return trees, tokens


def test_expr_nodes_is_the_grammars_own_set():
    trees, _tokens = _root_kinds("expr")
    assert trees == set(EXPR_NODES)


def test_an_expression_is_always_a_tree():
    """Every call site tests `isinstance(child, Tree)` before the membership test.

    That is only sound while no expression inlines all the way down to a bare Token.
    `?postfix` is aliased to `maybe_call`, which is what stops it.
    """
    _trees, tokens = _root_kinds("expr")
    assert tokens == set()


def test_a_type_node_is_never_an_expression_node():
    """A `let` and a `foreach` hold a type and an expression side by side.

    Both readers scan the direct children, so the two sets must not meet.
    """
    assert set(EXPR_NODES).isdisjoint(TYPE_NODE_NAMES)


def test_type_node_names_is_the_grammars_own_set_too():
    """The same derivation over `type` reproduces `TYPE_NODE_NAMES`.

    It is what makes the derivation above trustworthy: one algorithm, two sets, and a
    hand-written set that has been right all along on the other side.
    """
    trees, tokens = _root_kinds("type")
    assert trees == set(TYPE_NODE_NAMES)
    assert tokens == set()
