"""T1.1 AST-builder gate: lambda literals build a `Lambda` node.

Drives the real parser + ASTBuilder via ``parse_to_ast`` and asserts the built
node shape for both body forms (expression + block), typed and bare params, and
the zero-param ``|~|`` form. This is a front-end gate: it does NOT assert the
lambda compiles end to end (capture analysis, lifting, and codegen land in later
T1 phases).
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.ast import Lambda, Let, Block, BinaryOp, Param


def _lambda_in_main(body_line: str) -> Lambda:
    """Parse `fn main` with a single `let f = <lambda>` and return the Lambda."""
    src = (
        "fn main() i32:\n"
        f"    {body_line}\n"
        "    return Result.Ok(0)\n"
    )
    program, _tree = parse_to_ast(src)
    main = next(f for f in program.functions if f.name == "main")
    let = main.body.statements[0]
    assert isinstance(let, Let), f"expected Let, got {type(let).__name__}"
    assert isinstance(let.value, Lambda), f"expected Lambda, got {type(let.value).__name__}"
    return let.value


def test_expr_body_typed_param() -> None:
    lam = _lambda_in_main("let f = |i32 x| x + 1")
    assert lam.is_block_body is False
    assert len(lam.params) == 1
    p = lam.params[0]
    assert isinstance(p, Param)
    assert p.name == "x"
    assert str(p.ty) == "i32"
    # Expression body desugars later; the parsed body is the raw expression.
    assert isinstance(lam.body, BinaryOp)


def test_expr_body_bare_param_untyped() -> None:
    lam = _lambda_in_main("let g = |x| x * 2")
    assert lam.is_block_body is False
    assert len(lam.params) == 1
    assert lam.params[0].name == "x"
    # Bare param: type is inferred later, so it is None at parse time.
    assert lam.params[0].ty is None


def test_zero_param_tilde() -> None:
    lam = _lambda_in_main("let lazy = |~| compute()")
    assert lam.is_block_body is False
    assert lam.params == []


def test_multi_bare_params() -> None:
    lam = _lambda_in_main("let k = |a, b| a + b")
    assert [p.name for p in lam.params] == ["a", "b"]
    assert all(p.ty is None for p in lam.params)


def test_mixed_typed_and_bare_params() -> None:
    lam = _lambda_in_main("let m = |i32 a, b| a + b")
    assert [p.name for p in lam.params] == ["a", "b"]
    assert str(lam.params[0].ty) == "i32"
    assert lam.params[1].ty is None


def test_block_body() -> None:
    src = (
        "fn main() i32:\n"
        "    let h = |x|:\n"
        "        let i32 y = x * 2\n"
        "        return Result.Ok(y)\n"
        "    return Result.Ok(0)\n"
    )
    program, _tree = parse_to_ast(src)
    main = next(f for f in program.functions if f.name == "main")
    lam = main.body.statements[0].value
    assert isinstance(lam, Lambda)
    assert lam.is_block_body is True
    assert isinstance(lam.body, Block)
    assert len(lam.body.statements) == 2


def test_block_body_with_return_annotation() -> None:
    src = (
        "fn main() i32:\n"
        "    let h = |i32 x| -> i32:\n"
        "        return Result.Ok(x + 1)\n"
        "    return Result.Ok(0)\n"
    )
    program, _tree = parse_to_ast(src)
    main = next(f for f in program.functions if f.name == "main")
    lam = main.body.statements[0].value
    assert isinstance(lam, Lambda)
    assert lam.is_block_body is True
    assert str(lam.ret) == "i32"
