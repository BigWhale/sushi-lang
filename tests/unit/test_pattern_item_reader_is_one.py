"""Gate: one reader reads a `pattern_item`, and a shape it cannot read is an ICE (#635).

`parse_pattern` and `parse_own_pattern` read the same node. Both dispatch on the same
four inner shapes: a nested `pattern`, a `wildcard_pattern`, an `own_pattern_call` and a
bare NAME. A shape neither of them can read is a malformed tree, so the answer is CE0002
with a location. A binding that goes away without a word is not an answer: it makes
`Pattern.bindings` shorter than the payload, and the arity check downstream then reports
the count of the payload instead of the real fault.
"""
from __future__ import annotations

import pytest
from lark import Token, Tree

from sushi_lang.internals.diagnostics import AstBuilderICE
from sushi_lang.internals.parser import build_parser
from sushi_lang.semantics.ast import NomBinding, OwnPattern, Pattern, RefBinding
from sushi_lang.semantics.ast_builder.builder import ASTBuilder
from sushi_lang.semantics.ast_builder.statements.matching import (
    parse_own_pattern, parse_pattern,
)


def _match_source(arm: str) -> str:
    return (
        "enum Shape:\n"
        "    Circle(i32)\n"
        "\n"
        "enum Wrap:\n"
        "    One(i32)\n"
        "\n"
        "fn main() i32:\n"
        "    let Shape s = Shape.Circle(1)\n"
        "    match s:\n"
        f"        {arm} -> println(\"one\")\n"
        "        _ -> println(\"other\")\n"
        "    return Result.Ok(0)\n"
    )


def _parse(arm: str) -> Tree:
    return build_parser().parse(_match_source(arm))


def _first(tree: Tree, data: str) -> Tree:
    for node in tree.iter_subtrees():
        if node.data == data:
            return node
    raise AssertionError(f"no `{data}` node in the parse tree")


def _outer_pattern(tree: Tree) -> Tree:
    """The arm's own pattern, not one nested inside it."""
    arm = _first(tree, "match_arm")
    for child in arm.children:
        if isinstance(child, Tree) and child.data == "pattern":
            return child
    raise AssertionError("no `pattern` on the match arm")


def _child(node: Tree, data: str) -> Tree:
    """A direct child of `node`. `iter_subtrees` walks bottom-up, so a nested node of
    the same kind would come first and the test would read the wrong one."""
    for child in node.children:
        if isinstance(child, Tree) and child.data == data:
            return child
    raise AssertionError(f"no direct `{data}` child")


def _outer_own(tree: Tree) -> Tree:
    """The arm's own `Own(...)`, not one nested inside it."""
    items = _child(_outer_pattern(tree), "pattern_list")
    return _child(_child(items, "pattern_item"), "own_pattern_call")


# A token no reader dispatches on. The wildcard arrives as a `wildcard_pattern` tree
# today, so a bare UNDERSCORE under a `pattern_item` is the drift shape: the grammar
# and the builder disagree, and nothing in the source can say so.
def _make_unreadable(item: Tree) -> None:
    item.children = [Token("UNDERSCORE", "_")]


def test_a_pattern_reads_a_bare_name_item() -> None:
    tree = _parse("Shape.Circle(r)")
    pattern = parse_pattern(_outer_pattern(tree), ASTBuilder())
    assert pattern.bindings == ["r"]


def test_a_pattern_reads_a_wildcard_item() -> None:
    tree = _parse("Shape.Circle(_)")
    pattern = parse_pattern(_outer_pattern(tree), ASTBuilder())
    assert pattern.bindings == ["_"]


def test_a_pattern_reads_a_nested_pattern_item() -> None:
    tree = _parse("Wrap.One(Shape.Circle(r))")
    pattern = parse_pattern(_outer_pattern(tree), ASTBuilder())
    assert len(pattern.bindings) == 1
    inner = pattern.bindings[0]
    assert isinstance(inner, Pattern)
    assert (inner.enum_name, inner.variant_name, inner.bindings) == ("Shape", "Circle", ["r"])


def test_a_pattern_reads_an_own_pattern_item() -> None:
    tree = _parse("Wrap.One(Own(h))")
    pattern = parse_pattern(_outer_pattern(tree), ASTBuilder())
    assert len(pattern.bindings) == 1
    inner = pattern.bindings[0]
    assert isinstance(inner, OwnPattern)
    assert inner.inner_pattern == "h"


def test_a_pattern_item_the_reader_cannot_read_is_an_ice_with_a_location() -> None:
    tree = _parse("Shape.Circle(r)")
    _make_unreadable(_first(tree, "pattern_item"))

    with pytest.raises(AstBuilderICE) as caught:
        parse_pattern(_outer_pattern(tree), ASTBuilder())

    assert caught.value.code == "CE0002"
    assert caught.value.span is not None


def test_an_own_pattern_reads_the_same_four_shapes() -> None:
    cases = {
        "Wrap.One(Own(h))": "h",
        "Wrap.One(Own(_))": "_",
    }
    for arm, expected in cases.items():
        tree = _parse(arm)
        own = parse_own_pattern(_outer_own(tree), ASTBuilder())
        assert own.inner_pattern == expected

    tree = _parse("Wrap.One(Own(Shape.Circle(r)))")
    own = parse_own_pattern(_outer_own(tree), ASTBuilder())
    assert isinstance(own.inner_pattern, Pattern)
    assert own.inner_pattern.variant_name == "Circle"

    tree = _parse("Wrap.One(Own(Own(h)))")
    own = parse_own_pattern(_outer_own(tree), ASTBuilder())
    assert isinstance(own.inner_pattern, OwnPattern)
    assert own.inner_pattern.inner_pattern == "h"


def test_an_own_pattern_item_the_reader_cannot_read_is_an_ice_with_a_location() -> None:
    tree = _parse("Wrap.One(Own(h))")
    own_call = _outer_own(tree)
    _make_unreadable(_first(own_call, "pattern_item"))

    with pytest.raises(AstBuilderICE) as caught:
        parse_own_pattern(own_call, ASTBuilder())

    assert caught.value.code == "CE0002"
    assert caught.value.span is not None


def test_a_binding_mode_survives_the_shared_reader() -> None:
    """A mode marker is read beside the `pattern_item`, never inside it."""
    tree = _parse("Shape.Circle(poke r)")
    pattern = parse_pattern(_outer_pattern(tree), ASTBuilder())
    assert len(pattern.bindings) == 1
    ref = pattern.bindings[0]
    assert isinstance(ref, RefBinding)
    assert (ref.name, ref.mode) == ("r", "poke")

    tree = _parse("Shape.Circle(nom r)")
    pattern = parse_pattern(_outer_pattern(tree), ASTBuilder())
    assert len(pattern.bindings) == 1
    nom = pattern.bindings[0]
    assert isinstance(nom, NomBinding)
    assert nom.name == "r"

    tree = _parse("Wrap.One(Own(poke h))")
    own = parse_own_pattern(_outer_own(tree), ASTBuilder())
    assert (own.inner_pattern, own.inner_borrow) == ("h", "poke")
