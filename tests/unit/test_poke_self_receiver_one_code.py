"""One fault, one diagnostic: the receiver of a `poke self` call (#776, #777).

`EXPECT_ERROR_CODE` is a substring test, so a fixture cannot say that a shape prints
exactly ONE error code. This gate counts them on the analyze path. A temporary receiver
is CE2429's rule (the write lands on a copy); the typecheck pass must not add CE2404 for
the same fault.
"""
import pytest

_PRELUDE = """\
struct Counter:
    i32 n

struct Outer:
    Counter inner

extend Counter bump(poke self) ~:
    self.n := self.n + 1

fn make() Counter:
    return Result.Ok(Counter(1))

fn mko() Outer:
    return Result.Ok(Outer(Counter(1)))

"""

# shape -> the one error code it must print, or None for a legal call.
_SHAPES = {
    "clone_receiver": ("c.clone().bump()", "CE2429"),
    "try_receiver": ("make()??.bump()", "CE2429"),
    "construction_receiver": ("Counter(3).bump()", "CE2429"),
    "try_then_field_receiver": ("mko()??.inner.bump()", "CE2429"),
    "unhandled_channel_receiver": ("make().bump()", "CE2515"),
    "fixed_element_receiver": ("xs[0].bump()", None),
    "field_of_element_receiver": ("os[0].inner.bump()", None),
}


def _program(line: str) -> str:
    return (_PRELUDE + "fn main() i32:\n"
            "    let Counter c = Counter(1)\n"
            "    let Counter[2] xs = [Counter(1), Counter(2)]\n"
            "    let Outer[] os = from([Outer(Counter(1))])\n"
            f"    {line}\n"
            "    println(\"{c.n} {xs[0].n} {os[0].inner.n}\")\n"
            "    return Result.Ok(0)\n")


def _errors(reporter):
    return [d for d in reporter.items if d.kind == "error"]


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_a_receiver_shape_prints_one_error_code(analyze, shape):
    line, code = _SHAPES[shape]
    codes = [d.code for d in _errors(analyze(_program(line), name="m"))]
    expected = [] if code is None else [code]
    assert codes == expected, f"{line!r}: expected {expected}, got {codes}"


def test_the_boundary_note_is_not_the_primary_span(analyze):
    errors = _errors(analyze(_program("c.clone().bump()"), name="m"))
    assert [d.code for d in errors] == ["CE2429"]
    note = next(s for s in errors[0].sub if s.kind == "note")
    assert note.span != errors[0].span, (note.span, errors[0].span)
