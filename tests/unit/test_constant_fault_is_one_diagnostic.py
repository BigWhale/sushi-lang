"""One fault in a constant initializer, one diagnostic (#710).

Two readers walk the same initializer -- the scope pass, which owns "what kind of name is
this", and the constant evaluator, which folds it -- and both spoke. A name that reaches
nothing answered CE1001 from the pass and CE1002 from the evaluator, and CE1002 reads
"assignment to undeclared variable" about a READ. A cycle answered CE0109 once per member,
because the typecheck pass validates every constant in turn and each walk finds the same
loop from its own side; CE2095 has reported one type cycle once since #677.

`EXPECT_ERROR_CODE` matches a SUBSTRING of the whole output, so no fixture can pin either
rule. This module counts the codes.
"""
from __future__ import annotations

import pytest

_MAIN = "\n\nfn main() i32:\n    return Result.Ok(0)\n"


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def test_an_unknown_name_in_a_constant_reads_one_code(analyze):
    codes = _codes(analyze("const i32 X = missing" + _MAIN, name="m"))
    assert codes == ["CE1001"], codes


def test_an_unknown_operand_in_a_constant_reads_one_code(analyze):
    codes = _codes(analyze("const i32 X = missing + 1" + _MAIN, name="m"))
    assert codes == ["CE1001"], codes


@pytest.mark.parametrize("declaration,name", [
    ("struct Box:\n    i32 n\n", "Box"),
    ("enum Shade:\n    Dim(i32)\n", "Shade"),
])
def test_a_type_name_in_a_constant_reads_one_code(analyze, declaration, name):
    """The scope pass says CE2105 here, and it says the whole of it."""
    codes = _codes(analyze(f"{declaration}\nconst i32 X = {name}" + _MAIN, name="m"))
    assert codes == ["CE2105"], codes


def test_a_function_name_in_a_constant_reads_the_type_mismatch(analyze):
    """No pass refuses the NAME, so the rule that owns the fault gets to speak."""
    source = "fn helper() i32:\n    return Result.Ok(1)\n\nconst i32 X = helper" + _MAIN
    assert _codes(analyze(source, name="m")) == ["CE2002"]


def test_a_two_constant_cycle_is_reported_once(analyze):
    codes = _codes(analyze("const i32 A = B\nconst i32 B = A" + _MAIN, name="m"))
    assert codes == ["CE0109"], codes


def test_a_three_constant_cycle_is_reported_once(analyze):
    source = "const i32 A = B\nconst i32 B = C\nconst i32 C = A" + _MAIN
    assert _codes(analyze(source, name="m")) == ["CE0109"]


def test_each_cycle_of_two_is_reported(analyze):
    """One diagnostic per CYCLE, and a program may hold more than one."""
    source = ("const i32 A = B\nconst i32 B = A\n"
              "const i32 C = D\nconst i32 D = C" + _MAIN)
    assert _codes(analyze(source, name="m")) == ["CE0109", "CE0109"]
