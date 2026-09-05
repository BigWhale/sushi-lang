"""A constraint violation is ONE diagnostic, located, with the constraint as its note (#579).

`EXPECT_ERROR_CODE` is a substring check and cannot pin "one fault, one diagnostic", so the
code SET is pinned here on the analyze fixture. Ruling 4: after a CE4006 the whole-program
analysis stops, the CE2095 precedent, so no template copy is cut for the refused
instantiation and the use site adds nothing.
"""
from __future__ import annotations

USER_TEMPLATE = """
perk Loud:
    fn shout() i32

struct Box@(T: Loud):
    T item

extend Box@(T) yell() i32:
    return self.item.shout()

fn main() i32:
    let Box@(i32) b = Box(5)
    println("{b.yell()}")
    return Result.Ok(0)
"""

GENERIC_FUNCTION = """
perk Loud:
    fn shout() i32

fn f@(T: Loud)(T x) i32:
    return Result.Ok(x.shout())

fn main() i32:
    println("{f(5).realise(0)}")
    return Result.Ok(0)
"""

GENERIC_ENUM = """
perk Loud:
    fn shout() i32

enum Slot@(T: Loud):
    Filled(T)
    Empty

fn main() i32:
    let Slot@(string) s = Slot.Empty
    return Result.Ok(0)
"""


def _errors(reporter):
    return [d for d in reporter.items if d.kind == "error"]


def test_a_struct_violation_is_one_located_diagnostic_with_a_note(analyze):
    reporter = analyze(USER_TEMPLATE)
    errors = _errors(reporter)
    assert [d.code for d in errors] == ["CE4006"]
    only = errors[0]
    assert only.span is not None, "the type that names the instantiation has a location"
    assert only.span.line == 12
    notes = [sub for sub in only.sub if sub.kind == "note"]
    assert notes and notes[0].span is not None
    assert notes[0].span.line == 5, "the note points at the constraint"


def test_a_function_violation_is_one_diagnostic(analyze):
    reporter = analyze(GENERIC_FUNCTION)
    assert [d.code for d in _errors(reporter)] == ["CE4006"]


def test_an_enum_violation_is_one_located_diagnostic(analyze):
    reporter = analyze(GENERIC_ENUM)
    errors = _errors(reporter)
    assert [d.code for d in errors] == ["CE4006"]
    assert errors[0].span is not None and errors[0].span.line == 10
