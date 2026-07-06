"""T1.3 gate: lambda type-checking, capture legality (CE2094), invariance.

Uses the `analyze` fixture (full semantic pipeline, no backend). A lambda infers a
FunctionType from its params (declared, or bare params inferred from the binding's
expected FunctionType) and body; capture is excluded from type identity, so a
capturing lambda still matches `fn(i32) -> i32`. Capturing a borrow is CE2094; a
shape mismatch is CE2002 on assignment.
"""
from __future__ import annotations


def _errors(reporter) -> list[str]:
    return [d.code for d in reporter.items if d.kind == "error"]


def test_capturing_typed_param_type_checks_clean(analyze):
    src = (
        "fn main() i32:\n"
        "    let i32 n = 5\n"
        "    let fn(i32) -> i32 f = |i32 x| x + n\n"
        "    println(f(10).realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    assert _errors(analyze(src)) == []


def test_bare_param_inferred_from_annotation(analyze):
    # `|x|` has no declared type; it is inferred from the let's fn(i32) -> i32.
    src = (
        "fn main() i32:\n"
        "    let i32 n = 7\n"
        "    let fn(i32) -> i32 f = |x| x + n\n"
        "    println(f(20).realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    assert _errors(analyze(src)) == []


def test_non_capturing_lambda_type_checks_clean(analyze):
    src = (
        "fn main() i32:\n"
        "    let fn(i32) -> i32 f = |i32 x| x * 2\n"
        "    println(f(21).realise(0))\n"
        "    return Result.Ok(0)\n"
    )
    assert _errors(analyze(src)) == []


def test_borrow_capture_is_CE2094(analyze):
    # Capturing a &peek borrow through a closure is deferred to Tier 2.
    src = (
        "fn use_ref(&peek i32 r) i32:\n"
        "    let fn(i32) -> i32 g = |i32 x| x + r\n"
        "    return Result.Ok(g(1).realise(0))\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2094" in _errors(analyze(src))


def test_assignment_shape_mismatch_is_CE2002(analyze):
    # Lambda is fn(i32) -> i32 but the annotation is fn(string) -> i32 (invariant).
    src = (
        "fn main() i32:\n"
        "    let fn(string) -> i32 f = |i32 x| x + 1\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2002" in _errors(analyze(src))
