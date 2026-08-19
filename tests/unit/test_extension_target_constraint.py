"""A concrete type argument in an extension target is a CONSTRAINT (#393).

`extend Box@(i32)` applies to `Box<i32>` and to nothing else, exactly as a perk
implementation on the same target already did. The argument used to be stored as a
type-parameter NAME and substituted positionally, so the method registered for every
instantiation of the base type: it answered a `Box@(string)` receiver, and it was a CE0000
as soon as the body touched the type.

The gate reads the MONOMORPHIZED copies rather than a diagnostic. A copy is what dispatch
resolves to, so counting them says which instantiations the declaration claims -- and a
declaration that claims one it should not is invisible in a program that instantiates only
one type argument, which is why the #389 batch passed over the same defect.
"""
from __future__ import annotations

import pytest

_BOX = """\
struct Box@(T):
    T value

"""

_TWO_INSTANTIATIONS = """\
fn main() i32:
    let Box@(i32) a = Box(1)
    let Box@(string) b = Box("marvin")
    return Result.Ok(0)
"""


def _targets(analysis, method_name: str) -> list[str]:
    """The target type name of every monomorphized copy of one method."""
    analyzer = analysis.analyzer
    assert analyzer is not None, "analysis produced no analyzer"
    return sorted(
        str(getattr(copy.target_type, "name", copy.target_type))
        for copy in analyzer.monomorphized_extensions
        if copy.name == method_name
    )


def _errors(analysis) -> list[str]:
    return [f"{d.code} {d.message}" for d in analysis.reporter.items if d.kind == "error"]


def test_a_concrete_target_claims_only_its_own_instantiation(analyze_program):
    """`extend Box@(i32)` is monomorphized for `Box<i32>` and for nothing else."""
    analysis = analyze_program(_BOX + """\
extend Box@(i32) tag() i32:
    return 1

""" + _TWO_INSTANTIATIONS, name="concrete_scope")

    assert _targets(analysis, "tag") == ["Box<i32>"], (
        "the declared `i32` constrained nothing: the method was monomorphized for every "
        f"instantiation of Box, so it answers a Box@(string) receiver too "
        f"(targets: {_targets(analysis, 'tag')})"
    )
    assert not analysis.reporter.has_errors, _errors(analysis)


def test_a_template_target_still_claims_every_instantiation(analyze_program):
    """The template case is unchanged -- one copy per instantiation."""
    analysis = analyze_program(_BOX + """\
extend Box@(T) tag() i32:
    return 1

""" + _TWO_INSTANTIATIONS, name="template_scope")

    assert _targets(analysis, "tag") == ["Box<i32>", "Box<string>"], (
        "a fully generic target applies to every instantiation; got "
        f"{_targets(analysis, 'tag')}"
    )
    assert not analysis.reporter.has_errors, _errors(analysis)


def test_two_concrete_targets_share_one_method_name(analyze_program):
    """Two types, two methods -- not one template declared twice."""
    analysis = analyze_program(_BOX + """\
extend Box@(i32) tag() i32:
    return self.value

extend Box@(string) tag() i32:
    return self.value.len()

""" + _TWO_INSTANTIATIONS, name="two_concretes")

    assert not analysis.reporter.has_errors, (
        "two distinct concrete targets are two methods on two different types; the table "
        "keyed on the base name alone and rejected the second as a duplicate: "
        + "; ".join(_errors(analysis))
    )
    assert _targets(analysis, "tag") == ["Box<i32>", "Box<string>"], (
        f"each declaration serves its own target; got {_targets(analysis, 'tag')}"
    )


# The overlap and the partial form, with the declaration order that reaches each branch of
# the collector's check. Both used to be accepted or mis-reported.
_REJECTED = [
    (
        "template_then_concrete",
        "extend Box@(T) tag() i32:\n    return 1\n\n"
        "extend Box@(i32) tag() i32:\n    return 2\n",
        "CE0101",
        "Box@(i32)",
    ),
    (
        "concrete_then_template",
        "extend Box@(i32) tag() i32:\n    return 2\n\n"
        "extend Box@(T) tag() i32:\n    return 1\n",
        "CE0101",
        "Box@(T)",
    ),
    (
        "same_concrete_twice",
        "extend Box@(i32) tag() i32:\n    return 1\n\n"
        "extend Box@(i32) tag() i32:\n    return 2\n",
        "CE0101",
        "Box@(i32)",
    ),
]


@pytest.mark.parametrize("case_id,declarations,code,rendered", _REJECTED,
                         ids=[c[0] for c in _REJECTED])
def test_an_overlapping_target_is_rejected_and_names_the_target(
        analyze_program, case_id, declarations, code, rendered):
    """Overlap is rejected, and the diagnostic renders the arguments it turns on."""
    analysis = analyze_program(_BOX + declarations + "\n" + _TWO_INSTANTIATIONS,
                               name=case_id)

    reported = [d for d in analysis.reporter.items if d.code == code]
    assert reported, (
        f"{case_id}: expected {code}; got " + "; ".join(_errors(analysis) or ["nothing"])
    )
    assert any(rendered in d.message for d in reported), (
        f"{case_id}: the diagnostic must name '{rendered}'. It elided the arguments as "
        "'Box@(...)', which cannot tell an overlap from two distinct concrete targets: "
        + "; ".join(d.message for d in reported)
    )


def test_a_partially_concrete_target_is_rejected(analyze_program):
    """`extend Pair@(i32, U)` -- name every parameter, or make every argument concrete."""
    analysis = analyze_program("""\
struct Pair@(A, B):
    A first
    B second

extend Pair@(i32, U) tag() i32:
    return self.first

fn main() i32:
    let Pair@(i32, string) p = Pair(7, "marvin")
    return Result.Ok(0)
""", name="partial_target")

    codes = [d.code for d in analysis.reporter.items if d.kind == "error"]
    assert "CE2098" in codes, (
        "a partially-concrete target compiled. Rejecting it is what keeps two equally "
        f"specific targets from ever needing an ordering rule (codes: {codes})"
    )


def test_the_relational_diagnostic_carries_the_first_declaration(analyze_program):
    """An overlap exists only because of another declaration, so it renders both."""
    analysis = analyze_program(_BOX + """\
extend Box@(T) tag() i32:
    return 1

extend Box@(i32) tag() i32:
    return 2

""" + _TWO_INSTANTIATIONS, name="relational")

    reported = [d for d in analysis.reporter.items if d.code == "CE0101"]
    assert reported, "expected CE0101"
    assert any(d.sub for d in reported), (
        "a relational error rendered without its second location is a bug: the other "
        "declaration is the half the user cannot see from the reported line"
    )
