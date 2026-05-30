"""Direct unit tests for return-reachability analysis (passes/types/control_flow).

block_always_returns / statement_always_returns decide whether every code path
through a block or statement ends in a return; the CE0107 "missing return" check
relies on them. The analysis is pure (it inspects AST node shapes only and never
touches the validator), so these tests parse real source through the production
parser and call the functions with a None validator.
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.passes.types.control_flow import (
    block_always_returns,
    statement_always_returns,
)


def _functions(src: str) -> dict:
    """Parse src and return a name -> FuncDef map."""
    program, _tree = parse_to_ast(src if src.endswith("\n") else src + "\n")
    return {fn.name: fn for fn in program.functions}


def test_plain_return_always_returns():
    fns = _functions(
        "fn plain() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert block_always_returns(None, fns["plain"].body) is True


def test_if_with_else_all_branches_return():
    # Every branch (then + else) returns, so the if returns on all paths.
    fns = _functions(
        "fn allret(i32 x) i32:\n"
        "    if (x > 0):\n"
        "        return Result.Ok(1)\n"
        "    else:\n"
        "        return Result.Ok(2)\n"
    )
    assert block_always_returns(None, fns["allret"].body) is True
    if_stmt = fns["allret"].body.statements[0]
    assert statement_always_returns(None, if_stmt) is True


def test_if_without_else_does_not_always_return():
    # No else branch: the falsey path falls through without returning.
    fns = _functions(
        "fn maybe(i32 x) i32:\n"
        "    if (x > 0):\n"
        "        return Result.Ok(1)\n"
        "    return Result.Ok(0)\n"
    )
    if_stmt = fns["maybe"].body.statements[0]
    assert statement_always_returns(None, if_stmt) is False


def test_if_with_else_missing_return_in_one_branch():
    # else branch lacks a return, so a path through the if does not return.
    fns = _functions(
        "fn missing(i32 x) i32:\n"
        "    if (x > 0):\n"
        "        return Result.Ok(1)\n"
        "    else:\n"
        "        println(\"x\")\n"
    )
    if_stmt = fns["missing"].body.statements[0]
    assert statement_always_returns(None, if_stmt) is False
    assert block_always_returns(None, fns["missing"].body) is False


def test_loops_never_guarantee_return():
    # A while body that returns still does not guarantee a return (loop may not run).
    fns = _functions(
        "fn loopy() i32:\n"
        "    while (true):\n"
        "        return Result.Ok(1)\n"
    )
    assert block_always_returns(None, fns["loopy"].body) is False


def test_match_all_arms_return():
    fns = _functions(
        "enum Color:\n"
        "    Red\n"
        "    Green\n"
        "fn m(Color c) i32:\n"
        "    match c:\n"
        "        Color.Red -> return Result.Ok(1)\n"
        "        Color.Green -> return Result.Ok(2)\n"
    )
    assert block_always_returns(None, fns["m"].body) is True


def test_match_one_arm_missing_return():
    fns = _functions(
        "enum Color:\n"
        "    Red\n"
        "    Green\n"
        "fn m2(Color c) i32:\n"
        "    match c:\n"
        "        Color.Red -> return Result.Ok(1)\n"
        "        Color.Green -> println(\"hi\")\n"
    )
    assert block_always_returns(None, fns["m2"].body) is False


def test_empty_block_does_not_return():
    # A block with no returning statement does not always return.
    fns = _functions(
        "fn noret() ~:\n"
        "    let i32 x = 1\n"
        "    return Result.Ok(~)\n"
    )
    body = fns["noret"].body
    # The let statement alone does not return.
    assert statement_always_returns(None, body.statements[0]) is False
