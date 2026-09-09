"""A unit's diagnostics read in source order, whichever pass found them (#629).

The per-unit passes run one after another over the whole unit -- scope, then typecheck,
then lift, then borrow -- so the order they emit in is pass order, not source order. A
reader wants the file. One helper orders a unit's items before they join the program
reporter; a file with no line to stand on keeps the order it was found in.
"""
from __future__ import annotations

from sushi_lang.internals.report import Diagnostic, Span, in_source_order


def head_lines(reporter, code: str) -> list[int]:
    """The line of every diagnostic carrying `code`, in report order."""
    return [d.span.line for d in reporter.items
            if d.code == code and d.span is not None]


def make(code: str, line: int, col: int = 1, filename: str = "a.sushi") -> Diagnostic:
    return Diagnostic("error", code, code, Span(line, col, line, col), filename=filename)


def test_a_lambda_body_fault_reads_before_a_later_line(analyze):
    """The bug this order rule answers: the lift pass finds a lambda body fault after
    every other fault of the unit, so line 2 was printed after line 6."""
    reporter = analyze("""fn a() i32:
    let fn(i32) -> i32 f = |i32 n| missing_one(n)
    return Result.Ok(f(1).realise(0))

fn b() i32:
    return Result.Ok(missing_two(2))

fn main() i32:
    println(a().realise(0) + b().realise(0))
    return Result.Ok(0)
""")
    assert head_lines(reporter, "CE2008") == [2, 6]


def test_two_faults_of_one_line_keep_the_column_order(analyze):
    reporter = analyze("""fn main() i32:
    let fn(i32) -> i32 f = |i32 n|:
        return missing_fn(n)
    println(f(1).realise(0))
    return Result.Ok(0)
""")
    found = [(d.code, d.span.col) for d in reporter.items
             if d.code in ("CE2008", "CE2030") and d.span is not None]
    assert found == sorted(found, key=lambda pair: pair[1]), found


def test_the_helper_orders_by_line_then_column():
    ordered = in_source_order([make("CE2", 6, 5), make("CE1", 2, 9), make("CE0", 2, 3)])
    assert [(d.code, d.span.line, d.span.col) for d in ordered] == [
        ("CE0", 2, 3), ("CE1", 2, 9), ("CE2", 6, 5)]


def test_the_helper_keeps_a_file_together_in_the_order_it_was_first_seen():
    """Not alphabetical: a file is grouped where the passes first reached it, so a
    library template's findings never jump ahead of the unit that pulled it in."""
    items = [make("CE0", 9, filename="z.sushi"),
             make("CE1", 4, filename="a.sushi"),
             make("CE2", 2, filename="z.sushi")]
    ordered = in_source_order(items)
    assert [(d.filename, d.span.line) for d in ordered] == [
        ("z.sushi", 2), ("z.sushi", 9), ("a.sushi", 4)]


def test_the_helper_puts_a_diagnostic_with_no_span_first_in_its_file():
    """A finding about the file as a whole has no line to sort by; it heads its group."""
    spanless = Diagnostic("error", "CE9", "CE9", None, filename="a.sushi")
    ordered = in_source_order([make("CE0", 4), spanless, make("CE1", 2)])
    assert [d.code for d in ordered] == ["CE9", "CE1", "CE0"]


def test_the_helper_is_stable_for_one_position():
    """Two findings at one caret keep the order the passes made them in."""
    ordered = in_source_order([make("CE1", 3, 7), make("CE0", 3, 7)])
    assert [d.code for d in ordered] == ["CE1", "CE0"]
