"""The AST builder batches its diagnostics, and hands them over in source order.

A rule with an honest substitute value reports the fault and keeps building, so one
run shows every such fault (#641). `EXPECT_ERROR_CODE` is a substring test and cannot
pin a COUNT, which is the whole point of the change, so the count lives here.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter


def build(src: str) -> Reporter:
    """Parse `src` with a Reporter and answer what the builder reported."""
    reporter = Reporter(source=src, filename="main.sushi")
    parse_to_ast(src, reporter=reporter)
    return reporter


def codes(reporter: Reporter) -> list:
    return [(d.code, d.span.line if d.span else None) for d in reporter.items]


THREE_LITERALS = """fn main() i32:
    let i32 a = 1__000
    let i32 b = 2__000
    let i32 c = 3__000
    return Result.Ok(0)
"""


def test_three_bad_literals_are_three_diagnostics():
    assert codes(build(THREE_LITERALS)) == [
        ("CE6006", 2), ("CE6006", 3), ("CE6006", 4)]


def test_one_fault_is_still_one_diagnostic():
    src = """fn main() i32:
    let i32 a = 1__000
    return Result.Ok(0)
"""
    assert codes(build(src)) == [("CE6006", 2)]


def test_a_clean_unit_reports_nothing():
    src = """fn main() i32:
    let i32 a = 1_000
    return Result.Ok(a)
"""
    assert build(src).items == []


def test_with_no_reporter_the_first_fault_is_still_raised():
    """Every caller that parses a source of its own reads one exception, as before."""
    with pytest.raises(SyntaxDiagnostic) as caught:
        parse_to_ast(THREE_LITERALS)
    assert caught.value.code == "CE6006"
    assert caught.value.span.line == 2


def test_the_findings_come_out_in_source_order():
    """The builder reads the constants ahead of the declarations; a reader wants the file."""
    src = """fn main() i32:
    let i32 a = 1__000
    return Result.Ok(0)

const i32 LATER = 2__000
"""
    assert codes(build(src)) == [("CE6006", 2), ("CE6006", 5)]


def test_a_fatal_rule_joins_the_batch_in_source_order():
    """A nested `fn` cannot recover, and it is still told in its place."""
    src = """fn main() i32:
    let i32 a = 1__000
    fn inner() i32:
        return Result.Ok(0)
    return Result.Ok(0)
"""
    assert codes(build(src)) == [("CE6006", 2), ("CE6101", 3)]


def test_a_fatal_rule_stops_the_walk_after_it():
    """Nothing past the fatal fault is read, so only what came before it is told."""
    src = """fn main() i32:
    fn inner() i32:
        return Result.Ok(0)
    let i32 a = 1__000
    return Result.Ok(0)
"""
    assert codes(build(src)) == [("CE6101", 2)]


RECOVERED = {
    "CE6006": """fn main() i32:
    let i32 a = 1__000
    let i32 b = 2__000
    return Result.Ok(0)
""",
    "CE2071": """fn main() i32:
    let i32 a = 077
    let i32 b = 088
    return Result.Ok(0)
""",
    "CE2099": """fn main() i32:
    let i32[0] a = [1]
    let i32[0] b = [1]
    return Result.Ok(0)
""",
    "CE2418": """fn one(peek peek i32 x) ~:
    return Result.Ok(~)

fn two(peek peek i32 x) ~:
    return Result.Ok(~)

fn main() i32:
    return Result.Ok(0)
""",
    "CE2424": """enum Inner:
    Deep(i32[])

enum Outer:
    Wrap(Inner)

fn main() i32:
    let Outer o = Outer.Wrap(Inner.Deep(from([1])))
    match o:
        Outer.Wrap(Inner.Deep(poke p)) -> println("one")
    match o:
        Outer.Wrap(Inner.Deep(poke q)) -> println("two")
    return Result.Ok(0)
""",
    "CE2425": """fn one(poke count) ~:
    return Result.Ok(~)

fn two(poke count) ~:
    return Result.Ok(~)

fn main() i32:
    return Result.Ok(0)
""",
    "CE2434": """enum Holder:
    Full(Own@(i32))

fn main() i32:
    let Own@(i32) o = Own.alloc(7)
    let Holder h = Holder.Full(o)
    match h:
        Holder.Full(Own(nom a)) -> println("one")
    match h:
        Holder.Full(Own(nom b)) -> println("two")
    return Result.Ok(0)
""",
    "CE6103": """perk Loud:
    fn shout() string
    fn tag() string

struct Crate:
    i32 weight

extend Crate with Loud:
    public fn shout() string:
        return "loud"
    public fn tag() string:
        return "c"

fn main() i32:
    return Result.Ok(0)
""",
}


@pytest.mark.parametrize("code", sorted(RECOVERED))
def test_a_recoverable_rule_finds_the_second_fault(code):
    reported = [c for c, _line in codes(build(RECOVERED[code]))]
    assert reported == [code, code]


@pytest.mark.parametrize("code", sorted(RECOVERED))
def test_a_recoverable_rule_still_raises_without_a_reporter(code):
    with pytest.raises(SyntaxDiagnostic) as caught:
        parse_to_ast(RECOVERED[code])
    assert caught.value.code == code


FATAL = {
    "CE6101": """fn main() i32:
    fn inner() i32:
        return Result.Ok(0)
    return Result.Ok(0)
""",
    "CE2026": """fn main() i32:
    println("open {a")
    return Result.Ok(0)
""",
    "CE2038": """fn main() i32:
    println("empty {}")
    return Result.Ok(0)
""",
    "CE6010": """fn main() i32:
    println("bad {1 +}")
    return Result.Ok(0)
""",
}


@pytest.mark.parametrize("code", sorted(FATAL))
def test_a_fatal_rule_is_told_once_and_stops_the_walk(code):
    """No honest substitute exists, so the walk ends and the one finding is told."""
    assert [c for c, _line in codes(build(FATAL[code]))] == [code]
