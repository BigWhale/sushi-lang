"""A generic function's body is one source, so it answers one report (#648).

Every monomorphized instance carries the TEMPLATE's spans, and the per-unit passes walk
the instances as ordinary functions -- correctly, because a per-instance truth can only be
found there. What must not follow is the count: one fault in the body used to answer once
per instantiation, at one caret.

The rule is the one `_check_monomorphized_extensions` already applies to a generic-target
extension: identity is the kind, the code, the MESSAGE, the file and the span, so a
finding that genuinely differs by type argument keeps its own message and is still told.
"""
from __future__ import annotations

from sushi_lang.internals.report import Diagnostic, Reporter, Span


TYPECHECK_FAULT = """fn f@(T)(T v) i32:
    return Result.Ok(missing_fn(1))

fn main() i32:
    println(f(3).realise(0))
    println(f("x").realise(0))
    return Result.Ok(0)
"""

SCOPE_WARNING = """fn f@(T)(T v) i32:
    let i32 unused = 7
    return Result.Ok(1)

fn main() i32:
    println(f(3).realise(0))
    println(f("x").realise(0))
    return Result.Ok(0)
"""

BORROW_FAULT = """fn eat(nom i32[] xs) ~:
    println(xs.len())
    return Result.Ok(~)

fn f@(T)(T v) i32:
    let i32[] owned = from([1, 2])
    eat(nom owned)
    eat(nom owned)
    return Result.Ok(1)

fn main() i32:
    println(f(3).realise(0))
    println(f("y").realise(0))
    return Result.Ok(0)
"""

THREE_INSTANTIATIONS = """fn f@(T)(T v) i32:
    return Result.Ok(missing_fn(1))

fn main() i32:
    println(f(3).realise(0))
    println(f("x").realise(0))
    println(f(2.5).realise(0))
    return Result.Ok(0)
"""

ONE_INSTANTIATION = """fn f@(T)(T v) i32:
    return Result.Ok(missing_fn(1))

fn main() i32:
    println(f(3).realise(0))
    return Result.Ok(0)
"""

DIFFERS_BY_TYPE_ARGUMENT = """fn f@(T)(T v) i32:
    return Result.Ok(v + 1)

fn main() i32:
    let f64 a = 2.5
    let u8 b = 3
    println(f(a).realise(0))
    println(f(b).realise(0))
    return Result.Ok(0)
"""

LAMBDA_IN_A_GENERIC_BODY = """fn f@(T)(T v) i32:
    let fn(i32) -> i32 g = |i32 n| missing_fn(n)
    return Result.Ok(g(1).realise(0))

fn main() i32:
    println(f(3).realise(0))
    println(f("x").realise(0))
    return Result.Ok(0)
"""


def count(reporter, code: str) -> int:
    """How many diagnostics carry `code`."""
    return sum(1 for d in reporter.items if d.code == code)


def messages(reporter, code: str) -> set[str]:
    return {d.message for d in reporter.items if d.code == code}


def test_a_typecheck_fault_reports_once_for_two_instantiations(analyze):
    assert count(analyze(TYPECHECK_FAULT), "CE2008") == 1


def test_a_scope_warning_reports_once_for_two_instantiations(analyze):
    """CW1001 is not a different fault -- an error and a warning repeated alike. Two
    unused names, so two warnings, and neither of them twice."""
    reporter = analyze(SCOPE_WARNING)
    told = [d.message for d in reporter.items if d.code == "CW1001"]
    assert sorted(told) == sorted(set(told)), told
    assert len(told) == 2, told


def test_a_borrow_fault_reports_once_for_two_instantiations(analyze):
    """The borrow pass walks the TEMPLATE as well as the instances, so this one was
    N + 1 rather than N."""
    assert count(analyze(BORROW_FAULT), "CE2405") == 1


def test_three_instantiations_still_report_once(analyze):
    """The count tracked what the CALLER asked for, which is not the reader's business."""
    assert count(analyze(THREE_INSTANTIATIONS), "CE2008") == 1


def test_one_instantiation_still_reports(analyze):
    """Control: collapsing a repeat must not swallow the only copy."""
    assert count(analyze(ONE_INSTANTIATION), "CE2008") == 1


def test_a_fault_that_differs_by_type_argument_reports_for_each(analyze):
    """The guard on the rule. Both instances fault at ONE caret with DIFFERENT messages
    -- `f64 and i32`, `u8 and i32` -- and each is a truth about its own instance."""
    reporter = analyze(DIFFERS_BY_TYPE_ARGUMENT)
    assert count(reporter, "CE2510") == 2
    assert len(messages(reporter, "CE2510")) == 2


def test_a_lambda_in_a_generic_body_reports_once(analyze):
    """A lambda lifts once per instance, each copy carrying the template's spans (#629)."""
    assert count(analyze(LAMBDA_IN_A_GENERIC_BODY), "CE2008") == 1


def test_a_reporter_keeps_a_repeat_by_default():
    """Collapsing is scoped to an instance body: nothing else loses a second copy."""
    reporter = Reporter(filename="a.sushi")
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    assert len(reporter.items) == 2


def test_a_reporter_collapses_a_repeat_while_reading_an_instance_body():
    reporter = Reporter(filename="a.sushi")
    reporter.collapse_repeats = True
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.error("CE1", "other", Span(1, 1, 1, 1))
    assert [d.message for d in reporter.items] == ["same", "other"]


def test_a_collapsed_reporter_keeps_what_was_already_told():
    """A fault the TEMPLATE raised is the same fault, so the instance adds nothing."""
    reporter = Reporter(filename="a.sushi")
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.collapse_repeats = True
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    assert len(reporter.items) == 1


def test_the_identity_is_the_kind_code_message_file_and_span():
    from sushi_lang.internals.report import diagnostic_identity

    one = Diagnostic("error", "CE1", "m", Span(1, 2, 1, 3), filename="a.sushi")
    same = Diagnostic("error", "CE1", "m", Span(1, 2, 1, 3), filename="a.sushi")
    other_file = Diagnostic("error", "CE1", "m", Span(1, 2, 1, 3), filename="b.sushi")
    assert diagnostic_identity(one) == diagnostic_identity(same)
    assert diagnostic_identity(one) != diagnostic_identity(other_file)
