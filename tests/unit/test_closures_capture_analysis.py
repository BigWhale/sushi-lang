"""T1.2 gate: the scope pass records a lambda's captured free names.

A capture is a name used in the lambda body that resolves to an ENCLOSING local
(a variable of the surrounding function), not to the lambda's own params/locals,
and not to a global (top-level fn, constant, enum). We drive the real parser +
ScopeAnalyzer and inspect `Lambda.captures` (a list of `Param`, names only at
this stage; types are filled by the type pass in T1.3).
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.scope import ScopeAnalyzer
from sushi_lang.semantics.ast import Lambda, Let


def _capture_names(src: str, fn_name: str = "main") -> list[str]:
    program, _ = parse_to_ast(src)
    ScopeAnalyzer(Reporter(source=src)).run(program)
    fn = next(f for f in program.functions if f.name == fn_name)
    lam = next(
        s.value for s in fn.body.statements
        if isinstance(s, Let) and isinstance(s.value, Lambda)
    )
    assert lam.captures is not None
    return [p.name for p in lam.captures]


def test_captures_enclosing_local() -> None:
    src = (
        "fn main() i32:\n"
        "    let i32 x = 5\n"
        "    let f = |y| x + y\n"
        "    return Result.Ok(0)\n"
    )
    assert _capture_names(src) == ["x"]


def test_no_capture_when_only_own_param_used() -> None:
    src = (
        "fn main() i32:\n"
        "    let f = |y| y + 1\n"
        "    return Result.Ok(0)\n"
    )
    assert _capture_names(src) == []


def test_does_not_capture_toplevel_function() -> None:
    # `helper` is a top-level fn, not an enclosing local -> not a capture.
    src = (
        "fn helper(i32 a) i32:\n"
        "    return Result.Ok(a + 1)\n"
        "fn main() i32:\n"
        "    let g = |y| helper(y)\n"
        "    return Result.Ok(0)\n"
    )
    assert _capture_names(src) == []


def test_captures_multiple_locals_in_order() -> None:
    src = (
        "fn main() i32:\n"
        "    let i32 a = 1\n"
        "    let i32 b = 2\n"
        "    let f = |y| a + b + y\n"
        "    return Result.Ok(0)\n"
    )
    assert _capture_names(src) == ["a", "b"]


def test_capture_deduplicated() -> None:
    src = (
        "fn main() i32:\n"
        "    let i32 x = 3\n"
        "    let f = |y| x + x + y\n"
        "    return Result.Ok(0)\n"
    )
    assert _capture_names(src) == ["x"]


def test_nested_lambda_propagates_capture() -> None:
    # An inner lambda's reference to an outer-function local must be captured by
    # BOTH lambdas: the outer closure carries the value so the inner can use it.
    src = (
        "fn main() i32:\n"
        "    let i32 x = 5\n"
        "    let outer = |a|:\n"
        "        let inner = |b| x + b\n"
        "        return Result.Ok(a)\n"
        "    return Result.Ok(0)\n"
    )
    program, _ = parse_to_ast(src)
    ScopeAnalyzer(Reporter(source=src)).run(program)
    main = next(f for f in program.functions if f.name == "main")
    outer = main.body.statements[1].value
    assert isinstance(outer, Lambda)
    assert [p.name for p in outer.captures] == ["x"]
    inner = outer.body.statements[0].value
    assert isinstance(inner, Lambda)
    assert [p.name for p in inner.captures] == ["x"]


def test_lambda_local_let_is_not_captured() -> None:
    # A `let` INSIDE the lambda body binds locally -> not a capture.
    src = (
        "fn main() i32:\n"
        "    let i32 x = 5\n"
        "    let f = |y|:\n"
        "        let i32 t = y * 2\n"
        "        return Result.Ok(t + x)\n"
        "    return Result.Ok(0)\n"
    )
    assert _capture_names(src) == ["x"]
