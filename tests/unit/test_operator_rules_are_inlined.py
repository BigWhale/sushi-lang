"""An operator rule with one child leaves no node behind.

The eleven operator-chain rules used to carry no `?`, so Lark kept every one of them
even where it held a single child. `print(1)` allocated eleven Trees over one integer,
and 56 per cent of a parse tree was that chain. The AST never saw the difference --
`bin_chain` answered `items[0]`, and `handle_cast` and `parse_range_expr` both
short-circuited -- so nothing but the tree size measured it, and nothing but this gate
says when a `?` goes missing again.
"""
from __future__ import annotations

import pathlib

from lark import Tree

from sushi_lang.internals.parser import build_parser

# The rules that chain one operator level to the next. Each one has to inline where it
# holds a single child, and each one still has to make its own node where it does not.
OPERATOR_RULES = frozenset({
    "or_expr", "xor_expr", "and_expr",
    "bitwise_or", "bitwise_xor", "bitwise_and",
    "equality", "comparison", "range", "shift", "cast",
})

SOURCES = [
    """fn main() i32:
    print(1)
    println(1 + 2)
    let i32 a = 3 * 4
    a := a as i64 as i32
    foreach(i in 0..a):
        println(i)
    if (a > 2 and a < 99):
        println(a & 1)
    return Result.Ok(0)
""",
]


def _walk(tree: Tree):
    yield tree
    for child in tree.children:
        if isinstance(child, Tree):
            yield from _walk(child)


def _trees(source: str):
    return list(_walk(build_parser().parse(source)))


def _corpus() -> list[str]:
    root = pathlib.Path(__file__).resolve().parents[2]
    iter_module = root / "sushi_lang/sushi_stdlib/src_sushi/collections/iter.sushi"
    return SOURCES + [iter_module.read_text()]


def test_no_operator_rule_survives_with_one_child():
    for source in _corpus():
        wrappers = [t.data for t in _trees(source)
                    if t.data in OPERATOR_RULES and len(t.children) == 1]
        assert wrappers == []


def test_an_operand_reaches_its_statement_with_no_wrapper():
    """`print(1)` hands the print statement the operand itself."""
    tree = build_parser().parse("fn main() i32:\n    print(1)\n    return Result.Ok(0)\n")
    printed = next(t for t in _walk(tree) if t.data == "print_stmt")
    assert [c.data for c in printed.children if isinstance(c, Tree)] == ["maybe_call"]


def test_an_operator_still_makes_its_own_node():
    """The inlining is about the single-child case and nothing else."""
    tree = build_parser().parse("fn main() i32:\n    print(1 + 2)\n    return Result.Ok(0)\n")
    printed = next(t for t in _walk(tree) if t.data == "print_stmt")
    assert [c.data for c in printed.children if isinstance(c, Tree)] == ["add"]
