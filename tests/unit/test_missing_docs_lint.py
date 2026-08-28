"""`--warn-missing-docs`: the five completeness lints, and what each one does NOT do.

`docs/design/documentation.md` section 6 is the authority, and the phase-5 rulings settle
what section 6 left open. Three of them are asserted here:

- R29, every declaration warns, public and private. `public` is not the test, because an
  internal API is documented surface as much as an exported one.
- R30, two exemptions: `fn main()` and the FFI seam.
- R33, a block lint presupposes a block. CW7003, CW7004 and CW7005 fire only where a
  block already exists, so one omission is one diagnostic and the flag never becomes a
  wall.
"""
from __future__ import annotations

import pytest

MAIN = "fn main() i32:\n    return Result.Ok(0)\n"

# A unit block, so CW7006 does not stand in every source below.
UNIT = "##: The unit. :##\n\n"


def codes(reporter) -> list:
    return [item.code for item in reporter.items]


def messages(reporter, code: str) -> list:
    return [item.message for item in reporter.items if item.code == code]


# -- CW7002: a declaration with no block ----------------------------------------

@pytest.mark.parametrize("src,name", [
    ("fn helper(i32 a) i32:\n    return Result.Ok(a)\n", "helper"),
    ("const i32 ANSWER = 42\n", "ANSWER"),
    ("struct Ship:\n    i32 hull\n", "Ship"),
    ("enum Mood:\n    Happy\n", "Mood"),
    ("perk Greet:\n    fn greet(i32 n) i32\n", "Greet"),
    ("extend i32 squared() i32:\n    return self * self\n", "squared"),
    ("fn identity@(T)(nom T x) T:\n    return Result.Ok(x)\n", "identity"),
])
def test_an_undocumented_declaration_warns(analyze, src, name):
    reporter = analyze(UNIT + src + MAIN, warn_missing_docs=True)
    assert "CW7002" in codes(reporter)
    assert any(f"'{name}'" in message for message in messages(reporter, "CW7002")), (
        f"no CW7002 names '{name}': {messages(reporter, 'CW7002')}"
    )


def test_an_undocumented_struct_field_warns(analyze):
    src = UNIT + "##: A ship. :##\nstruct Ship:\n    i32 hull\n" + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert any("'hull'" in message for message in messages(reporter, "CW7002"))


def test_an_undocumented_enum_variant_warns(analyze):
    src = UNIT + "##: A mood. :##\nenum Mood:\n    Happy\n" + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert any("'Happy'" in message for message in messages(reporter, "CW7002"))


def test_an_undocumented_perk_method_warns(analyze):
    src = UNIT + "##: A perk. :##\nperk Greet:\n    fn greet(i32 n) i32\n" + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert any("'greet'" in message for message in messages(reporter, "CW7002"))


def test_an_undocumented_perk_implementation_warns(analyze):
    src = UNIT + (
        "##: A perk. :##\nperk Greet:\n"
        "    ##: Greets. :##\n    fn greet(i32 n) i32\n\n"
        "extend i32 with Greet:\n"
        "    ##: Greets. :##\n    fn greet(i32 n) i32:\n        return Result.Ok(n)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert any("Greet" in message for message in messages(reporter, "CW7002"))


def test_a_documented_declaration_does_not_warn(analyze):
    src = UNIT + "##: The answer. :##\nconst i32 ANSWER = 42\n" + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert messages(reporter, "CW7002") == []


def test_a_declaration_documented_from_inside_its_body_does_not_warn(analyze):
    src = UNIT + "fn helper(i32 a) i32:\n    ##: Helps. :##\n    return Result.Ok(a)\n" + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert messages(reporter, "CW7002") == []


# -- CW7002: the two exemptions (R30) -------------------------------------------

def test_main_is_exempt(analyze):
    reporter = analyze(UNIT + MAIN, warn_missing_docs=True)
    assert codes(reporter) == []


def test_the_ffi_seam_is_exempt(analyze):
    src = UNIT + (
        'unsafe external "C" as libc because "the test needs a seam":\n'
        '    fn abs(i32 n) i32 = "abs"\n\n'
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert messages(reporter, "CW7002") == []


# -- CW7003: a documented callable with an undocumented parameter ---------------

def test_an_undocumented_parameter_of_a_documented_callable_warns(analyze):
    src = UNIT + (
        "##:\nAdds.\n\n- Parameter a: The first addend.\n- Returns: The sum.\n:##\n"
        "fn add(i32 a, i32 b) i32:\n    return Result.Ok(a + b)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert "CW7003" in codes(reporter)
    assert any("'b'" in message for message in messages(reporter, "CW7003"))
    assert not any("'a'" in message for message in messages(reporter, "CW7003"))


def test_a_poke_self_receiver_is_never_demanded(analyze):
    """The builders strip `self` and lift it onto the declaration, so it is not a param."""
    src = UNIT + (
        "##: A counter. :##\nstruct Counter:\n    ##: The count. :##\n    i32 n\n\n"
        "##:\nBumps the counter.\n\n- Parameter by: How far to bump.\n:##\n"
        "extend Counter bump(poke self, i32 by) ~:\n"
        "    self.n := self.n + by\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert messages(reporter, "CW7003") == []


def test_a_fully_documented_callable_does_not_warn(analyze):
    src = UNIT + (
        "##:\nAdds.\n\n- Parameter a: The first addend.\n"
        "- Parameter b: The second addend.\n- Returns: The sum.\n:##\n"
        "fn add(i32 a, i32 b) i32:\n    return Result.Ok(a + b)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert codes(reporter) == []


# -- CW7004: a documented callable that returns a value, with no `- Returns:` ----

def test_a_documented_callable_with_no_returns_tag_warns(analyze):
    src = UNIT + (
        "##:\nAdds.\n\n- Parameter a: The first addend.\n"
        "- Parameter b: The second addend.\n:##\n"
        "fn add(i32 a, i32 b) i32:\n    return Result.Ok(a + b)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert "CW7004" in codes(reporter)
    assert any("'add'" in message for message in messages(reporter, "CW7004"))


def test_a_blank_return_needs_no_returns_tag(analyze):
    src = UNIT + (
        "##:\nShouts.\n\n- Parameter a: What to shout.\n:##\n"
        "fn shout(i32 a) ~:\n    println(\"{a}\")\n    return Result.Ok(~)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert messages(reporter, "CW7004") == []


# -- CW7005: a documented function declaring `| E`, with no `- Errors:` ---------

def test_a_documented_function_with_an_error_arm_and_no_errors_tag_warns(analyze):
    src = UNIT + (
        "##: A fault. :##\nenum DriveError:\n    ##: Divided by nought. :##\n    DivZero\n\n"
        "##:\nDivides.\n\n- Parameter a: The dividend.\n- Parameter b: The divisor.\n"
        "- Returns: The quotient.\n:##\n"
        "fn divide(i32 a, i32 b) i32 | DriveError:\n"
        "    if (b == 0):\n        return Result.Err(DriveError.DivZero)\n"
        "    return Result.Ok(a / b)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert "CW7005" in codes(reporter)
    assert any("'divide'" in message for message in messages(reporter, "CW7005"))


def test_a_function_with_no_error_arm_needs_no_errors_tag(analyze):
    src = UNIT + (
        "##:\nAdds.\n\n- Parameter a: The first addend.\n"
        "- Parameter b: The second addend.\n- Returns: The sum.\n:##\n"
        "fn add(i32 a, i32 b) i32:\n    return Result.Ok(a + b)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert messages(reporter, "CW7005") == []


# -- CW7006: a unit with no block ------------------------------------------------

def test_a_unit_with_no_block_warns(analyze):
    reporter = analyze(MAIN, warn_missing_docs=True)
    assert codes(reporter) == ["CW7006"]


def test_a_unit_with_a_block_does_not_warn(analyze):
    reporter = analyze(UNIT + MAIN, warn_missing_docs=True)
    assert "CW7006" not in codes(reporter)


# -- R33: a block lint presupposes a block ---------------------------------------

def test_an_undocumented_declaration_collects_cw7002_and_nothing_else(analyze):
    """One omission, one diagnostic. Without R33 this source reports four."""
    src = UNIT + (
        "##: A fault. :##\nenum DriveError:\n    ##: Divided by nought. :##\n    DivZero\n\n"
        "fn divide(i32 a, i32 b) i32 | DriveError:\n"
        "    if (b == 0):\n        return Result.Err(DriveError.DivZero)\n"
        "    return Result.Ok(a / b)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert codes(reporter) == ["CW7002"]


# -- the flag is a gate ----------------------------------------------------------

def test_nothing_is_reported_without_the_flag(analyze):
    src = (
        "fn divide(i32 a, i32 b) i32:\n    return Result.Ok(a / b)\n"
        "struct Ship:\n    i32 hull\n"
    ) + MAIN
    assert codes(analyze(src)) == []


def test_the_always_on_checks_still_run_under_the_flag(analyze):
    """Phase 5 must not move a CE70xx behind the flag; both sides report at once."""
    src = UNIT + (
        "##:\nAdds.\n\n- Parameter q: No such parameter.\n:##\n"
        "fn add(i32 a) i32:\n    return Result.Ok(a)\n"
    ) + MAIN
    reporter = analyze(src, warn_missing_docs=True)
    assert "CE7001" in codes(reporter)
    assert "CW7003" in codes(reporter)


# -- a library unit is never linted ----------------------------------------------

def test_a_library_unit_is_skipped(analyze_program, tmp_path):
    """R24: the docs loop skips a unit with a provenance, and the lint rides in it."""
    from sushi_lang.compiler.pipeline import _inject_source_stdlib_units
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.units import Unit, UnitManager

    program, _tree = parse_to_ast(UNIT + "use <collections/iter>\n\n" + MAIN)
    reporter = Reporter(filename="main.sushi")
    manager = UnitManager(root_path=tmp_path, reporter=reporter)
    manager.units["main"] = Unit(name="main", file_path=tmp_path / "main.sushi",
                                 ast=program, dependencies=[], public_symbols={})
    assert _inject_source_stdlib_units(manager, reporter) is True

    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
    manager.get_compilation_order()
    analyzer = SemanticAnalyzer(reporter, filename="main", unit_manager=manager,
                                warn_missing_docs=True)
    try:
        analyzer.check(program)
    except ValueError:
        pass
    assert [item.code for item in reporter.items if item.code.startswith("CW70")] == []
