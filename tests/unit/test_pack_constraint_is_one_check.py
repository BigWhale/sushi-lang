"""A pack element's constraint is judged by the ONE constraint check (#797).

The monomorphize step checks a leading parameter and every pack element alike, and a
refused instantiation cuts no copy. So a pack element that fails its constraint is ONE
diagnostic, CE2090 (the pack wording), with the constraint as a relational note, and
the template body adds no CE2008 cascade.
"""
from __future__ import annotations

_PROGRAM = """perk Named:
    fn name() string

extend i32 with Named:
    fn name() string:
        return "i32"

extend string with Named:
    fn name() string:
        return "string"

fn many@(...Ts: Named)(...Ts xs) ~:
    expand(a in xs):
        println(a.name())
    return Result.Ok(~)

fn main() i32:
    {call}
    return Result.Ok(0)
"""


def _errors(reporter):
    return [d for d in reporter.items if d.kind == "error"]


def _assert_one_pack_refusal(reporter, index, ty):
    errors = _errors(reporter)
    assert [d.code for d in errors] == ["CE2090"], [d.message for d in errors]
    only = errors[0]
    assert f"element {index}" in only.message and f"'{ty}'" in only.message
    assert only.span is not None and only.span.line == 18, "the caret is on the call"
    notes = [sub for sub in only.sub if sub.kind == "note"]
    assert notes and notes[0].span is not None
    assert notes[0].span.line == 12, "the note points at the pack's constraint"


def test_an_inferred_pack_refusal_is_one_diagnostic_with_a_note(analyze):
    _assert_one_pack_refusal(analyze(_PROGRAM.format(call="many(5, true)")), 1, "bool")


def test_an_explicit_pack_refusal_is_one_diagnostic_with_a_note(analyze):
    _assert_one_pack_refusal(
        analyze(_PROGRAM.format(call="many@(i32, bool)(5, true)")), 1, "bool")


def test_each_refused_element_is_named(analyze):
    errors = _errors(analyze(_PROGRAM.format(call="many(true, 5, 2.5)")))
    assert [d.code for d in errors] == ["CE2090", "CE2090"]
    assert "element 0" in errors[0].message and "'bool'" in errors[0].message
    assert "element 2" in errors[1].message and "'f64'" in errors[1].message


def test_a_satisfied_pack_is_silent(analyze):
    assert _errors(analyze(_PROGRAM.format(call='many(5, "five")'))) == []


def test_a_pack_reads_the_derived_answer_as_a_leading_parameter_does(analyze):
    source = """fn h@(...Ts: Hashable)(...Ts xs) ~:
    return Result.Ok(~)

fn main() i32:
    h(5, "five", true)
    return Result.Ok(0)
"""
    assert _errors(analyze(source)) == []
