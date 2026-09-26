"""A non-exhaustive `match` does not end the path (#886).

A value that no arm covers reaches the statement after the `match`, so that statement
is not dead (CE0140). The body does not certainly fall off its end either (CE0107):
the fault is the missing arm, and its exhaustiveness error is the one diagnostic.
An `EXPECT_ERROR_CODE` directive is a substring test and cannot prove that a code is
absent, so these checks read the whole code SET.
"""
from __future__ import annotations


ENUM = """enum Sign:
    Plus
    Minus
    Zero

"""

MAIN = """
fn main() i32:
    println("{f(1).realise(0)} {g(Sign.Zero).realise(0)}")
    return Result.Ok(0)
"""

STATEMENT_AFTER = ENUM + """fn f(i32 x) i32:
    match x:
        0 -> return Result.Ok(1)
        1 -> return Result.Ok(2)
    return Result.Ok(3)

fn g(Sign s) i32:
    match s:
        Sign.Plus -> return Result.Ok(1)
        Sign.Minus -> return Result.Ok(2)
    return Result.Ok(3)
""" + MAIN

AT_THE_END = ENUM + """fn f(i32 x) i32:
    match x:
        0 -> return Result.Ok(1)
        1 -> return Result.Ok(2)

fn g(Sign s) i32:
    match s:
        Sign.Plus -> return Result.Ok(1)
        Sign.Minus -> return Result.Ok(2)
""" + MAIN

NESTED_IN_AN_IF = ENUM + """fn f(i32 x) i32:
    if (x > 0):
        match x:
            1 -> return Result.Ok(1)
    else:
        return Result.Ok(0)
    return Result.Ok(3)

fn g(Sign s) i32:
    if (true):
        match s:
            Sign.Plus -> return Result.Ok(1)
            Sign.Minus -> return Result.Ok(2)
    else:
        return Result.Ok(0)
    return Result.Ok(3)
""" + MAIN

AN_ARM_FALLS_THROUGH = ENUM + """fn f(i32 x) i32:
    match x:
        0 -> return Result.Ok(1)
        1 -> println("one")

fn g(Sign s) i32:
    match s:
        Sign.Plus -> return Result.Ok(1)
        Sign.Minus -> println("minus")
""" + MAIN

EXHAUSTIVE_THEN_A_STATEMENT = ENUM + """fn f(i32 x) i32:
    match x:
        0 -> return Result.Ok(1)
        _ -> return Result.Ok(2)
    return Result.Ok(3)

fn g(Sign s) i32:
    match s:
        Sign.Plus -> return Result.Ok(1)
        Sign.Minus -> return Result.Ok(2)
        Sign.Zero -> return Result.Ok(0)
    return Result.Ok(3)
""" + MAIN


def codes(reporter) -> list[str]:
    return sorted(d.code for d in reporter.items)


def test_the_statement_after_is_not_dead(analyze):
    assert codes(analyze(STATEMENT_AFTER)) == ["CE2040", "CE2074"]


def test_a_body_that_ends_in_one_is_not_a_fall_off(analyze):
    assert codes(analyze(AT_THE_END)) == ["CE2040", "CE2074"]


def test_the_answer_carries_through_an_enclosing_if(analyze):
    assert codes(analyze(NESTED_IN_AN_IF)) == ["CE2040", "CE2074"]


def test_an_arm_that_falls_through_is_still_a_fall_off(analyze):
    assert codes(analyze(AN_ARM_FALLS_THROUGH)) == [
        "CE0107", "CE0107", "CE2040", "CE2074"]


def test_an_exhaustive_match_still_ends_the_path(analyze):
    reporter = analyze(EXHAUSTIVE_THEN_A_STATEMENT)
    assert codes(reporter) == ["CE0140", "CE0140"]
    assert sorted(d.span.line for d in reporter.items) == [10, 17]
