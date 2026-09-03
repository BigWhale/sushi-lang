"""One unhandled `Result` in a `let` is ONE diagnostic, and it is CE2505 (#535).

CE2505's text names the fix -- `.realise()` or a `match`. The general CE2002 names none,
and it used to be asked first, so a call right-hand side collected both codes at one
location while a channel method collected only the unhelpful one. The gate here is the
CODE SET, which is what neither an `EXPECT_ERROR_CODE` directive nor a caret can pin: the
directive is a substring assertion and passes on a second, wrong code beside the right one.
"""
from __future__ import annotations

import pytest

MAIN_HEAD = "fn main() i32:\n"
MAIN_TAIL = "    return Result.Ok(0)\n"


def codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def result_codes(reporter) -> list[str]:
    """Only the codes this rule owns, so an unrelated warning cannot fail the gate."""
    return [code for code in codes(reporter) if code in {"CE2002", "CE2505"}]


CALL = """fn tag(i32 n) i32:
    return Result.Ok(n)

fn main() i32:
    let i32 x = tag(1)
    println("{x}")
    return Result.Ok(0)
"""

CONSTRUCTOR = """fn main() i32:
    let i32 x = Result.Ok(42)
    println("{x}")
    return Result.Ok(0)
"""

EXTENSION_CHANNEL = """enum OddError:
    TooOdd

extend i32 half_checked() i32 | OddError:
    if (self % 2 == 1):
        return Result.Err(OddError.TooOdd)
    return self / 2

fn main() i32:
    let i32 half = 8.half_checked()
    println("{half}")
    return Result.Ok(0)
"""

PERK_CHANNEL = """enum SourceError:
    Closed

perk Source:
    fn read_one() i32 | SourceError

struct Counter:
    i32 value

extend Counter with Source:
    fn read_one() i32 | SourceError:
        return self.value

fn main() i32:
    let Counter c = Counter(1)
    let i32 x = c.read_one()
    println("{x}")
    return Result.Ok(0)
"""

ARRAY_RETURN = """fn create() i32[]:
    let i32[] arr = from([1, 2, 3])
    return Result.Ok(arr)

fn main() i32:
    let i32[] numbers = create()
    println("{numbers.len()}")
    return Result.Ok(0)
"""


@pytest.mark.parametrize("src,label", [
    (CALL, "a plain call"),
    (CONSTRUCTOR, "a Result constructor"),
    (EXTENSION_CHANNEL, "an extension channel method"),
    (PERK_CHANNEL, "a perk channel method"),
    (ARRAY_RETURN, "a call returning an array"),
])
def test_an_unhandled_result_is_one_ce2505(analyze, src, label):
    reporter = analyze(src)
    assert result_codes(reporter) == ["CE2505"], (
        f"{label} should answer CE2505 alone, got {result_codes(reporter)}"
    )


HANDLED = """fn tag(i32 n) i32:
    return Result.Ok(n)

fn main() i32:
    let i32 x = tag(42).realise(0)
    let string s = "towel"
    let string t = s.clone()
    println("{x} {t}")
    return Result.Ok(0)
"""

DECLARED_RESULT = """fn tag(i32 n) i32:
    return Result.Ok(n)

fn main() i32:
    let Result@(i32, StdError) r = tag(1)
    println("{r.is_ok()}")
    return Result.Ok(0)
"""


@pytest.mark.parametrize("src,label", [
    (HANDLED, ".realise() and .clone() unwrap"),
    (DECLARED_RESULT, "the declared type is the Result"),
])
def test_a_handled_result_is_no_diagnostic(analyze, src, label):
    reporter = analyze(src)
    assert result_codes(reporter) == [], (
        f"{label} needs no diagnostic, got {result_codes(reporter)}"
    )


MISMATCH = """fn main() i32:
    let string s = 42
    println("{s}")
    return Result.Ok(0)
"""


def test_a_plain_mismatch_still_answers_ce2002(analyze):
    """The Result question is asked first, and it declines to answer for a non-Result."""
    reporter = analyze(MISMATCH)
    assert result_codes(reporter) == ["CE2002"]
