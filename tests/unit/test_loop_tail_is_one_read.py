"""Gate: the three loop rules read their tail at one place, by rule position (#637).

`foreach_stmt`, `foreach_ref` and `expand_stmt` all close with `"in" expr ")" ":" block`.
The parser drops an anonymous literal, so the last two children are the iterable and the
block in every shape the grammar writes. `_loop_tail` is the one read of that pair.

The read is by POSITION and not by an index cursor. The cursor is what turned a missed
predicate into a compiler crash in this module before (#595): it stayed put, so the next
test read the wrong child. A position cannot stay put. A child that is not what the rule
fixes is drift, and drift stops here with a located CE0002 -- never an `IndexError`, and
never a silent skip.
"""
from __future__ import annotations

import pytest
from lark import Token, Tree

from sushi_lang.internals.diagnostics import AstBuilderICE
from sushi_lang.internals.parser import build_parser
from sushi_lang.semantics.ast import Expand, Foreach
from sushi_lang.semantics.ast_builder.builder import ASTBuilder
from sushi_lang.semantics.ast_builder.statements.loops import (
    parse_expand_stmt, parse_foreach_ref, parse_foreach_stmt,
)

# Every shape the two foreach rules write, plus the one `expand` writes. The keys name
# the optionals that are present.
FOREACH_SHAPES = {
    "name only": "foreach(x in 0..3):\n        println(x)\n",
    "type and name": "foreach(i32 x in 0..3):\n        println(x)\n",
    "name and marker": "foreach(x?? in 0..3):\n        println(x)\n",
    "type, name and marker": "foreach(i32 x?? in 0..3):\n        println(x)\n",
    "mode and name": "foreach(poke x in 0..3):\n        println(x)\n",
    "reference type and name": "foreach(poke i32 x in 0..3):\n        println(x)\n",
}

EXPAND_SHAPE = "expand(v in vals):\n        println(v)\n"

_PARSER = build_parser()


def _loop_nodes(body: str, rules: tuple[str, ...]) -> list[Tree]:
    src = f"fn f() ~:\n    {body}    return Result.Ok(~)\n"
    tree = _PARSER.parse(src)
    return [n for n in tree.iter_subtrees() if n.data in rules]


def _foreach_node(body: str) -> Tree:
    found = _loop_nodes(body, ("foreach_stmt", "foreach_ref"))
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("shape", sorted(FOREACH_SHAPES))
def test_a_foreach_puts_the_iterable_and_the_block_last(shape: str) -> None:
    node = _foreach_node(FOREACH_SHAPES[shape])

    iterable, block = node.children[-2], node.children[-1]
    assert isinstance(block, Tree) and block.data == "block"
    assert not (isinstance(iterable, Token) and iterable.type == "DOUBLE_QUESTION")


def test_an_expand_puts_the_iterable_and_the_block_last() -> None:
    found = _loop_nodes(EXPAND_SHAPE, ("expand_stmt",))
    assert len(found) == 1

    block = found[0].children[-1]
    assert isinstance(block, Tree) and block.data == "block"


@pytest.mark.parametrize("shape", sorted(FOREACH_SHAPES))
def test_every_foreach_shape_builds_one_node(shape: str) -> None:
    node = _foreach_node(FOREACH_SHAPES[shape])
    parse = parse_foreach_ref if node.data == "foreach_ref" else parse_foreach_stmt

    loop = parse(node, ASTBuilder())

    assert isinstance(loop, Foreach)
    assert loop.iterable is not None
    assert loop.body is not None


def test_an_expand_builds_one_node() -> None:
    node = _loop_nodes(EXPAND_SHAPE, ("expand_stmt",))[0]

    loop = parse_expand_stmt(node, ASTBuilder())

    assert isinstance(loop, Expand)
    assert loop.var == "v"


@pytest.mark.parametrize("shape", sorted(FOREACH_SHAPES))
def test_a_foreach_with_no_tail_is_a_located_ice(shape: str) -> None:
    """The head stands and the tail is gone. This is the read that had no guard."""
    node = _foreach_node(FOREACH_SHAPES[shape])
    node.children = node.children[:-2]
    parse = parse_foreach_ref if node.data == "foreach_ref" else parse_foreach_stmt

    with pytest.raises(AstBuilderICE) as caught:
        parse(node, ASTBuilder())

    assert caught.value.span is not None


def test_an_expand_with_no_tail_is_a_located_ice() -> None:
    node = _loop_nodes(EXPAND_SHAPE, ("expand_stmt",))[0]
    node.children = node.children[:-2]

    with pytest.raises(AstBuilderICE) as caught:
        parse_expand_stmt(node, ASTBuilder())

    assert caught.value.span is not None


def test_a_loop_with_no_keyword_is_a_located_ice() -> None:
    node = _foreach_node(FOREACH_SHAPES["name only"])
    node.children = node.children[1:]

    with pytest.raises(AstBuilderICE) as caught:
        parse_foreach_stmt(node, ASTBuilder())

    assert caught.value.code == "CE0002"
    assert caught.value.span is not None


def test_a_foreach_with_no_item_name_is_a_located_ice() -> None:
    node = _foreach_node(FOREACH_SHAPES["name only"])
    node.children = [node.children[0], *node.children[2:]]

    with pytest.raises(AstBuilderICE) as caught:
        parse_foreach_stmt(node, ASTBuilder())

    assert caught.value.code == "CE0002"
    assert caught.value.span is not None


def test_a_foreach_with_a_second_head_node_is_a_located_ice() -> None:
    node = _foreach_node(FOREACH_SHAPES["type and name"])
    node.children = [node.children[0], node.children[1], *node.children[1:]]

    with pytest.raises(AstBuilderICE) as caught:
        parse_foreach_stmt(node, ASTBuilder())

    assert caught.value.code == "CE0002"
    assert caught.value.span is not None


def test_an_expand_with_no_pack_name_is_a_located_ice() -> None:
    node = _loop_nodes(EXPAND_SHAPE, ("expand_stmt",))[0]
    node.children = [node.children[0], *node.children[2:]]

    with pytest.raises(AstBuilderICE) as caught:
        parse_expand_stmt(node, ASTBuilder())

    assert caught.value.code == "CE0002"
    assert caught.value.span is not None


def test_a_foreach_ref_with_no_mode_is_a_located_ice() -> None:
    node = _foreach_node(FOREACH_SHAPES["mode and name"])
    node.children = [node.children[0], *node.children[2:]]

    with pytest.raises(AstBuilderICE) as caught:
        parse_foreach_ref(node, ASTBuilder())

    assert caught.value.code == "CE0002"
    assert caught.value.span is not None
