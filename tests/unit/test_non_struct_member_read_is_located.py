"""A member read wants a struct, and every other receiver reads the same fault (#661).

#630 answered the STRUCT receiver with a located CE2106 and left every other receiver in
the backend. A member read on an array, on a primitive, on a string or on a closure walked
past each semantic pass and stopped at CE0031 -- tier 1, no file, no line, no caret, and
the note that says the fault is a bug in the compiler. It is a typo.

The refusal covers the kinds that carry NO field at all. The ENUM receiver joined them
with #666; `tests/unit/test_enum_member_read_is_refused.py` is its batch.
"""
from __future__ import annotations

BAG = """struct Bag:
    string label
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


def test_a_dynamic_array_receiver_answers_a_located_fault(analyze):
    """The issue's own program."""
    reporter = analyze("""fn main() i32:
    let i32[] rows = from([1, 2, 3])
    let i32 v = rows.nope
    println("{v}")
    return Result.Ok(0)
""")
    found = only(reporter, "CE2106")
    assert found.span is not None
    assert found.span.line == 3, found.span


def test_it_is_not_an_internal_error(analyze):
    """CE0031 renders as a compiler bug and blames the wrong person."""
    reporter = analyze("""fn main() i32:
    let i32[] rows = from([1, 2, 3])
    let i32 v = rows.nope
    println("{v}")
    return Result.Ok(0)
""")
    assert "CE0031" not in codes(reporter)


def test_a_fixed_array_receiver_answers_a_located_fault(analyze):
    reporter = analyze("""fn main() i32:
    let i32[3] rows = [1, 2, 3]
    let i32 v = rows.nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_string_receiver_answers_a_located_fault(analyze):
    reporter = analyze("""fn main() i32:
    let string s = "Mostly Harmless"
    let i32 v = s.nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_primitive_receiver_answers_a_located_fault(analyze):
    reporter = analyze("""fn main() i32:
    let i32 n = 42
    let i32 v = n.nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_closure_receiver_answers_a_located_fault(analyze):
    reporter = analyze("""fn main() i32:
    let fn(i32) -> i32 step = |i32 n| n + 1
    let i32 v = step.nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_non_struct_field_receiver_answers_a_located_fault(analyze):
    """A chain through a string field reached a second backend site, CE0044."""
    reporter = analyze(BAG + """
fn main() i32:
    let Bag b = Bag("towel")
    let i32 v = b.label.nope
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_an_assignment_target_answers_a_located_fault(analyze):
    reporter = analyze("""fn main() i32:
    let i32[] rows = from([1, 2, 3])
    rows.len := 9
    println("{rows[0]}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_reference_receiver_answers_a_located_fault(analyze):
    """A borrow reads the type it points at."""
    reporter = analyze("""fn look(peek i32 n) i32:
    return Result.Ok(n.nope)

fn main() i32:
    let i32 v = 1
    println("{look(peek v).realise(0)}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_builtin_method_read_says_what_the_name_is(analyze):
    """`builtin_method_exists` is the one seam that knows a compiler-defined method."""
    reporter = analyze("""fn main() i32:
    let string s = "Mostly Harmless"
    let i32 v = s.len
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert any("is a method, not a field" in note
               for note in notes(reporter, "CE2106")), notes(reporter, "CE2106")


def test_an_array_method_read_says_what_the_name_is(analyze):
    reporter = analyze("""fn main() i32:
    let i32[] rows = from([1, 2, 3])
    let i32 v = rows.extend
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert any("is a method, not a field" in note
               for note in notes(reporter, "CE2106")), notes(reporter, "CE2106")


def test_an_extension_method_read_on_a_primitive_says_what_the_name_is(analyze):
    """An extension on a built-in target reads the same note as a struct's."""
    reporter = analyze("""extend i32 doubled() i32:
    return self * 2

fn main() i32:
    let i32 n = 21
    let i32 v = n.doubled
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert any("is a method, not a field" in note
               for note in notes(reporter, "CE2106")), notes(reporter, "CE2106")


def test_a_method_call_is_untouched(analyze):
    """The parentheses are the whole difference."""
    reporter = analyze("""fn main() i32:
    let string s = "Mostly Harmless"
    let i32 v = s.len()
    println("{v}")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_an_array_element_read_is_untouched(analyze):
    reporter = analyze("""struct Point:
    i32 x
    i32 y

fn main() i32:
    let Point[] p = from([Point(10, 32)])
    println("{p[0].x}")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_a_namespaced_name_is_not_a_field_read(analyze):
    """A name behind an ALIAS's dot is a member of a namespace, not of a value."""
    reporter = analyze("""use <math> as m

fn main() i32:
    let f64 half = m.PI / 2.0
    println("{half}")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_a_bare_enum_variant_is_not_a_field_read(analyze):
    reporter = analyze("""enum Sign:
    Plus
    Minus

fn main() i32:
    let Sign s = Sign.Plus
    match s:
        Sign.Plus -> println("plus")
        Sign.Minus -> println("minus")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_an_entry_field_is_untouched(analyze):
    reporter = analyze("""use <collections/hashmap>

fn main() i32:
    let HashMap@(i32, string) map = HashMap.new()
    map.insert(1, "one")
    foreach(entry in map.entries()):
        println("{entry.key}: {entry.value}")
    map.free()
    return Result.Ok(0)
""")
    assert codes(reporter) == []
