"""A member read on an ENUM receiver is refused, and is not a silent wrong value (#666).

#661 gave every fieldless receiver a located CE2106 and left the enum alone, because the
answer here was a scope decision. A `Maybe@(T)` is an ordinary interned enum, so the read
compiled clean: the backend unwrapped the receiver to its payload struct and read field 0,
which answered the enum TAG. `pts.get(0).x` printed 0 where the value was 11, with no
diagnostic of any kind.

An enum carries variants, not fields. One rule answers all of them: the read is CE2106,
and the help says to unwrap. It closes the three tier-1 codes the fault reached as well --
CE0031 off a name, CE0029 through a wrapper the backend had already unwrapped, CE0067 off
a call.
"""
from __future__ import annotations

POINT = """struct Point:
    i32 x
    i32 y
"""

SIGN = """enum Sign:
    Plus
    Minus
"""


def codes(reporter) -> list[str]:
    return [d.code for d in reporter.items if d.kind == "error"]


def notes(reporter, code: str) -> list[str]:
    return [sub.message for d in reporter.items if d.code == code for sub in d.sub]


def only(reporter, code: str):
    """The one diagnostic carrying `code` -- and there must be exactly one."""
    found = [d for d in reporter.items if d.code == code]
    assert len(found) == 1, codes(reporter)
    return found[0]


def test_a_maybe_receiver_answers_a_located_fault(analyze):
    """The issue's own program: the read used to print the tag."""
    reporter = analyze(POINT + """
fn main() i32:
    let Point[] pts = from([Point(11, 22), Point(33, 44)])
    println("{pts.get(0).x}")
    return Result.Ok(0)
""")
    found = only(reporter, "CE2106")
    assert found.span is not None
    assert found.span.line == 7, found.span


def test_the_maybe_read_is_not_a_silent_pass(analyze):
    """It compiled clean and answered 0 where the value was 11."""
    reporter = analyze(POINT + """
fn main() i32:
    let Point[] pts = from([Point(11, 22), Point(33, 44)])
    println("{pts.get(0).x}")
    return Result.Ok(0)
""")
    assert codes(reporter) != []


def test_a_maybe_read_says_how_to_unwrap(analyze):
    reporter = analyze(POINT + """
fn main() i32:
    let Point[] pts = from([Point(11, 22)])
    println("{pts.get(0).x}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert any("realise" in note for note in notes(reporter, "CE2106")), \
        notes(reporter, "CE2106")


def test_a_plain_enum_receiver_answers_a_located_fault(analyze):
    reporter = analyze(SIGN + """
fn main() i32:
    let Sign s = Sign.Plus
    let i32 v = s.nope
    println("{v}")
    return Result.Ok(0)
""")
    found = only(reporter, "CE2106")
    assert found.span is not None
    assert found.span.line == 7, found.span


def test_a_plain_enum_receiver_is_not_an_internal_error(analyze):
    """CE0031 renders as a compiler bug and blames the wrong person."""
    reporter = analyze(SIGN + """
fn main() i32:
    let Sign s = Sign.Plus
    let i32 v = s.nope
    println("{v}")
    return Result.Ok(0)
""")
    assert "CE0031" not in codes(reporter)


def test_a_plain_enum_read_says_a_variant_is_not_a_field(analyze):
    """`??` and `.realise()` mean nothing on a user enum: a payload is read by a match."""
    reporter = analyze(SIGN + """
fn main() i32:
    let Sign s = Sign.Plus
    let i32 v = s.nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert any("match" in note for note in notes(reporter, "CE2106")), \
        notes(reporter, "CE2106")


def test_an_unknown_name_behind_a_maybe_is_not_an_internal_error(analyze):
    """CE0029 named the PAYLOAD struct: the backend had already unwrapped the receiver."""
    reporter = analyze(POINT + """
fn main() i32:
    let Point[] pts = from([Point(11, 22)])
    let i32 v = pts.get(0).nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert "CE0029" not in codes(reporter)


def test_a_result_receiver_off_a_call_is_not_an_internal_error(analyze):
    """CE0067 'cannot infer struct type from expression: Call'."""
    reporter = analyze(POINT + """
fn make() Point:
    return Result.Ok(Point(11, 22))

fn main() i32:
    let i32 v = make().nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert "CE0067" not in codes(reporter)


def test_a_result_receiver_names_the_wrapper_it_is(analyze):
    """The user reads the type they wrote, rendered back through `display_type`."""
    reporter = analyze(POINT + """
fn make() Point:
    return Result.Ok(Point(11, 22))

fn main() i32:
    let i32 v = make().x
    println("{v}")
    return Result.Ok(0)
""")
    found = only(reporter, "CE2106")
    assert "Result@(" in found.message, found.message
    assert "<" not in found.message, found.message


def test_a_wrapper_method_read_says_what_the_name_is(analyze):
    """A method note wins over the unwrap help: the parentheses are the whole fault."""
    reporter = analyze(POINT + """
fn main() i32:
    let Point[] pts = from([Point(11, 22)])
    let Point p = pts.get(0).realise
    println("{p.x}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert any("is a method, not a field" in note
               for note in notes(reporter, "CE2106")), notes(reporter, "CE2106")


def test_the_unwrapped_read_is_untouched(analyze):
    """`.realise()` answers the payload, and the payload has the field."""
    reporter = analyze(POINT + """
fn main() i32:
    let Point[] pts = from([Point(11, 22)])
    let Point ok = pts.get(0).realise(Point(0, 0))
    println("{ok.x}")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_a_propagated_read_is_untouched(analyze):
    """`??` answers the payload too."""
    reporter = analyze(POINT + """
fn first(Point[] pts) i32:
    let Point p = pts.get(0)??
    return Result.Ok(p.x)

fn main() i32:
    let Point[] pts = from([Point(11, 22)])
    println("{first(pts).realise(0)}")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_a_bare_variant_is_not_a_field_read(analyze):
    """`Sign.Plus` is a name behind a TYPE's dot, and it constructs."""
    reporter = analyze(SIGN + """
fn main() i32:
    let Sign s = Sign.Plus
    match s:
        Sign.Plus -> println("plus")
        Sign.Minus -> println("minus")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_a_bare_none_is_not_a_field_read(analyze):
    """`Maybe.None` against a declared `Maybe@(T)` is the same rule (#551)."""
    reporter = analyze("""fn main() i32:
    let Maybe@(i32) m = Maybe.None
    match m:
        Maybe.Some(v) -> println("{v}")
        Maybe.None -> println("none")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_a_payload_binding_is_untouched(analyze):
    """A match arm reads the payload, which is where a field of it lives."""
    reporter = analyze(POINT + """
enum Shape:
    Dot(Point)
    Nothing

fn main() i32:
    let Shape s = Shape.Dot(Point(11, 22))
    match s:
        Shape.Dot(p) -> println("{p.x}")
        Shape.Nothing -> println("nothing")
    return Result.Ok(0)
""")
    assert codes(reporter) == []
