"""P1-T1 parse spike: grammar for variadic-generic type packs + ``expand(...)``.

This task is a pure PARSE gate. It proves the two grammar additions

  (a) ``type_param: ELLIPSIS? NAME [perk_constraints]``  -- ``<...Ts>`` packs
  (b) ``expand_stmt: EXPAND "(" NAME "in" expr ")" ":" block``

parse unambiguously and without regressing any existing surface, BEFORE any
AST builder / semantic support exists (those are P1-T2/T3). We therefore drive
the raw Lark parser directly and never run the ASTBuilder over the new syntax.
"""
from __future__ import annotations

import pytest
from lark import Lark
from lark.exceptions import UnexpectedInput

from sushi_lang.internals.parser import GRAMMAR_PATH, ChainedPostlexer


def _parser() -> Lark:
    """Build the production LALR parser (same kwargs as parse_to_ast)."""
    return Lark.open(
        str(GRAMMAR_PATH),
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
        postlex=ChainedPostlexer(),
        lexer="basic",
    )


@pytest.fixture(scope="module")
def parser() -> Lark:
    return _parser()


# --------------------------------------------------------------------------- #
# ACCEPT: new surface (type-pack decls + value pack param + expand statement)
# --------------------------------------------------------------------------- #

ACCEPT_NEW = [
    # Constrained type-pack, value pack param, expand statement together.
    "fn f<...Ts: Display>(...Ts args) ~:\n"
    "    expand(a in args):\n"
    "        println(a)\n"
    "    return Result.Ok(~)\n",
    # Unconstrained type-pack.
    "fn g<...Ts>(...Ts xs) ~:\n"
    "    return Result.Ok(~)\n",
    # A bare expand statement inside an otherwise normal function.
    "fn h(i32[] items) ~:\n"
    "    expand(x in items):\n"
    "        println(x)\n"
    "    return Result.Ok(~)\n",
]


@pytest.mark.parametrize("src", ACCEPT_NEW)
def test_accepts_new_pack_and_expand_surface(parser: Lark, src: str) -> None:
    parser.parse(src)  # must not raise


# --------------------------------------------------------------------------- #
# ACCEPT: regression -- pre-existing surface must keep parsing unchanged
# --------------------------------------------------------------------------- #

ACCEPT_REGRESSION = [
    # v1 native variadic ``...T`` (reuses the untouched variadic_param rule).
    "fn log_all(string prefix, ...i32 values) ~:\n"
    "    return Result.Ok(~)\n",
    # Ordinary single generic type parameter (no ellipsis).
    "fn id<T>(T x) T:\n"
    "    return Result.Ok(x)\n",
    # Ordinary runtime foreach loop (shares the ``in`` keyword with expand).
    "fn loop(i32[] xs) ~:\n"
    "    foreach(i in xs.iter()):\n"
    "        println(i)\n"
    "    return Result.Ok(~)\n",
]


@pytest.mark.parametrize("src", ACCEPT_REGRESSION)
def test_accepts_existing_surface(parser: Lark, src: str) -> None:
    parser.parse(src)  # must not raise


# --------------------------------------------------------------------------- #
# REJECT: malformed forms must raise a parse error
# --------------------------------------------------------------------------- #

REJECT = [
    # expand with no iterable expression.
    "fn f(i32[] items) ~:\n"
    "    expand(a in):\n"
    "        println(a)\n"
    "    return Result.Ok(~)\n",
    # Ellipsis type-pack marker with no pack name.
    "fn f<...>(...i32 xs) ~:\n"
    "    return Result.Ok(~)\n",
    # expand missing the ``in`` keyword.
    "fn f(i32[] items) ~:\n"
    "    expand(a items):\n"
    "        println(a)\n"
    "    return Result.Ok(~)\n",
]


@pytest.mark.parametrize("src", REJECT)
def test_rejects_malformed(parser: Lark, src: str) -> None:
    with pytest.raises(UnexpectedInput):
        parser.parse(src)


# --------------------------------------------------------------------------- #
# Tree-shape pins for downstream P1-T2/T3 (loud failure if shapes drift)
# --------------------------------------------------------------------------- #

def test_type_pack_tree_shape(parser: Lark) -> None:
    """type_param of a pack carries the ELLIPSIS token as its first child."""
    src = "fn g<...Ts: Display>(...Ts xs) ~:\n    return Result.Ok(~)\n"
    tree = parser.parse(src)
    type_params = next(tree.find_data("type_param"))
    # children: ELLIPSIS token, NAME token, perk_constraints subtree
    assert str(type_params.children[0]) == "..."
    assert str(type_params.children[1]) == "Ts"


def test_expand_stmt_tree_shape(parser: Lark) -> None:
    """expand_stmt children: EXPAND token, loop-var NAME, iterable expr, block."""
    src = (
        "fn h(i32[] items) ~:\n"
        "    expand(a in items):\n"
        "        println(a)\n"
        "    return Result.Ok(~)\n"
    )
    tree = parser.parse(src)
    expand = next(tree.find_data("expand_stmt"))
    assert str(expand.children[0]) == "expand"     # EXPAND keyword token
    assert str(expand.children[1]) == "a"          # loop variable NAME
    # children[2] is the iterable expression subtree; children[-1] is the body.
    assert expand.children[-1].data == "block"
