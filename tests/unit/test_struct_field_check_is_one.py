"""One check over a struct construction's field arguments, two codes (#745).

The named constructor and the positional one held the same 48 lines: the field-type
resolution, the two propagation calls, the generic-callee rename and the compatibility
test. Only the code differs -- CE2083 for a named construction, CE2028 for a positional
one -- and the two texts are the same sentence.

The rows below are what the one loop has to keep answering, and the last one is what
stops the loop being written twice again.
"""
from __future__ import annotations

from pathlib import Path

from sushi_lang.semantics.passes.types.calls import structs as structs_module


# The variable is USED in every row, so no CW1001 rides along and each row reads
# the whole diagnostic list.
_MAIN_TAIL = "\n    println(\"{p.x}\")\n    return Result.Ok(0)\n"
_HOLDER_TAIL = "\n    println(\"{h.r.is_ok()}\")\n    return Result.Ok(0)\n"

_POINT = """\
struct Point:
    i32 x
    i32 y
"""

_HOLDER = """\
struct Holder:
    Result@(string, StdError) r

fn mk() string:
    return Result.Ok("hi")
"""


def _items(reporter) -> list[tuple[str, str]]:
    return [(item.code, item.message) for item in reporter.items]


def test_a_named_field_mismatch_reads_ce2083(analyze):
    source = _POINT + '\nfn main() i32:\n    let Point p = Point(x: 10, y: "twenty")' + _MAIN_TAIL
    assert _items(analyze(source, name="m")) == [
        ("CE2083", "field 'y' expects type 'i32', got 'string'")]


def test_a_positional_field_mismatch_reads_ce2028(analyze):
    source = _POINT + "\nfn main() i32:\n    let Point p = Point(10, true)" + _MAIN_TAIL
    assert _items(analyze(source, name="m")) == [
        ("CE2028", "field 'y' expects type 'i32', got 'bool'")]


def test_a_named_result_field_is_interned_and_fits(analyze):
    """#184: a `Result@(T, E)` field takes a call's return value, in both spellings."""
    source = _HOLDER + "\nfn main() i32:\n    let Holder h = Holder(r: mk())" + _HOLDER_TAIL
    assert _items(analyze(source, name="m")) == []


def test_a_positional_result_field_is_interned_and_fits(analyze):
    source = _HOLDER + "\nfn main() i32:\n    let Holder h = Holder(mk())" + _HOLDER_TAIL
    assert _items(analyze(source, name="m")) == []


def test_an_argument_past_the_last_field_is_still_validated(analyze):
    """The positional tail: an extra argument is checked, so its own fault is reported."""
    source = _POINT + "\nfn main() i32:\n    let Point p = Point(1, 2, missing)" + _MAIN_TAIL
    codes = [code for code, _ in _items(analyze(source, name="m"))]
    assert codes == ["CE2027", "CE1001"], codes


def test_the_field_argument_loop_is_written_once():
    """The gate: one loop, one resolution, one interning site, whatever the code is."""
    source = Path(structs_module.__file__).read_text(encoding="utf-8")
    assert source.count("types_compatible(") == 1, "the compatibility test is written twice"
    assert source.count("ensure_result_type_in_table(") == 1, "the interning is written twice"
    assert source.count("propagate_struct_type_to_dotcall(validator") == 1
