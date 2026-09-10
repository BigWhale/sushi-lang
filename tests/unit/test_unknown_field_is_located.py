"""A field no struct declares is the user's typo, and it reads like one (#630).

The `typecheck` pass did not reject an unknown field in a value position at all: the read
reached codegen, and the backend was the first thing to notice. It answered CE0029 -- tier
1, no file, no line, no caret, and the note that says the fault is a bug in the compiler.

CE2106 is the located answer. The four backend CE0029 sites stay where they are, and go
back to being the internal backstop they read as.
"""
from __future__ import annotations

BAG = """struct Bag:
    i32 weight
"""

MAKER = """fn mk() Bag:
    return Result.Ok(Bag(1))
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


def test_a_plain_receiver_answers_a_located_fault(analyze):
    """The issue's own program. A named local receiver, so nothing exotic is needed."""
    reporter = analyze(BAG + """
fn main() i32:
    let Bag b = Bag(1)
    let i32 v = b.n
    println("{v}")
    return Result.Ok(0)
""")
    found = only(reporter, "CE2106")
    assert found.span is not None
    assert found.span.line == 6, found.span


def test_it_is_not_an_internal_error(analyze):
    """CE0029 renders as a compiler bug and blames the wrong person."""
    reporter = analyze(BAG + """
fn main() i32:
    let Bag b = Bag(1)
    let i32 v = b.n
    println("{v}")
    return Result.Ok(0)
""")
    assert "CE0029" not in codes(reporter)


def test_a_try_receiver_answers_a_located_fault(analyze):
    """The #624 shape: a read off `??` used to stop earlier, at CE0067."""
    reporter = analyze(BAG + MAKER + """
fn main() i32:
    let i32 v = mk()??.n
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_construction_receiver_answers_a_located_fault(analyze):
    reporter = analyze(BAG + """
fn main() i32:
    let i32 v = Bag(2).n
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_an_assignment_target_answers_a_located_fault(analyze):
    """`backend/statements/variables.py` was the fourth CE0029 site."""
    reporter = analyze(BAG + """
fn main() i32:
    let Bag b = Bag(1)
    b.n := 3
    println("{b.weight}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_reference_binding_receiver_answers_a_located_fault(analyze):
    """`backend/expressions/borrow.py` was the third."""
    reporter = analyze(BAG + """
fn main() i32:
    let Bag b = Bag(1)
    let peek Bag r = b
    let i32 v = r.n
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_chained_read_answers_a_located_fault(analyze):
    reporter = analyze("""struct Inner:
    i32 depth

struct Outer:
    Inner inner

fn main() i32:
    let Outer o = Outer(Inner(1))
    let i32 v = o.inner.n
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")


def test_a_near_miss_suggests_the_field(analyze):
    """`suggest_member` is the one reader every position that can miss already quotes."""
    reporter = analyze(BAG + """
fn main() i32:
    let Bag b = Bag(1)
    let i32 v = b.weigth
    println("{v}")
    return Result.Ok(0)
""")
    only(reporter, "CE2106")
    assert any("weight" in note for note in notes(reporter, "CE2106")), \
        notes(reporter, "CE2106")


def test_a_known_field_is_accepted(analyze):
    reporter = analyze(BAG + """
fn main() i32:
    let Bag b = Bag(1)
    let i32 v = b.weight
    println("{v}")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_a_bare_enum_variant_is_not_a_field_read(analyze):
    """`Sign.Plus` is a MemberAccess too, and it constructs (#545)."""
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


def test_a_namespaced_constant_is_not_a_field_read(analyze):
    """A name behind an ALIAS's dot is a member of a namespace, not of a value."""
    reporter = analyze("""use <math> as m

fn main() i32:
    let f64 half = m.PI / 2.0
    println("{half}")
    return Result.Ok(0)
""")
    assert codes(reporter) == []


def test_an_entry_field_is_accepted(analyze):
    """A built-in struct's fields answer like any other's."""
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
