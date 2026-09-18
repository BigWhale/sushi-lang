"""One `extend Box@(X) ...` header, one answer, whichever path reads it (#698).

The extension path validated a concrete target's arguments not at all, and the perk
path refused a concrete target whose instantiation the program never names. One of each
pair was wrong. `EXPECT_ERROR_CODE` is a substring check, so the code SET is pinned here.
"""
from __future__ import annotations

DECLARATIONS = """
struct Box@(T):
    T v

struct Cage@(T):
    T v

struct Point:
    i32 x

perk Show:
    fn show() i32
"""

MAIN = """
fn main() i32:
    return Result.Ok(0)
"""

EXTENSION_GENERIC_ARGUMENT = DECLARATIONS + """
extend Box@(Cage) g() i32:
    return 1
""" + MAIN

PERK_GENERIC_ARGUMENT = DECLARATIONS + """
extend Box@(Cage) with Show:
    fn show() i32:
        return 1
""" + MAIN

EXTENSION_PERK_ARGUMENT = DECLARATIONS + """
extend Box@(Show) g() i32:
    return 1
""" + MAIN

PERK_PERK_ARGUMENT = DECLARATIONS + """
extend Box@(Show) with Show:
    fn show() i32:
        return 1
""" + MAIN

EXTENSION_UNNAMED_INSTANTIATION = DECLARATIONS + """
extend Box@(Point) g() i32:
    return 1
""" + MAIN

PERK_UNNAMED_INSTANTIATION = DECLARATIONS + """
extend Box@(Point) with Show:
    fn show() i32:
        return 1
""" + MAIN

TEMPLATE_TARGET = DECLARATIONS + """
extend Box@(T) g() i32:
    return 1
""" + MAIN

EXTENSION_UNKNOWN_BASE = DECLARATIONS + """
extend NoSuchBase@(i32) g() i32:
    return 1
""" + MAIN

PERK_UNKNOWN_BASE = DECLARATIONS + """
extend NoSuchBase@(i32) with Show:
    fn show() i32:
        return 1
""" + MAIN


def _codes(reporter):
    return [d.code for d in reporter.items if d.kind == "error"]


def test_a_generic_argument_without_its_own_arguments_is_one_ce2001(analyze):
    """`Cage` is a declared generic, so it names no type on its own."""
    assert _codes(analyze(EXTENSION_GENERIC_ARGUMENT)) == ["CE2001"]
    assert _codes(analyze(PERK_GENERIC_ARGUMENT)) == ["CE2001"]


def test_a_perk_argument_is_one_ce2001_on_both_paths(analyze):
    """A perk is a declared name and not a type, so it constrains nothing."""
    assert _codes(analyze(EXTENSION_PERK_ARGUMENT)) == ["CE2001"]
    assert _codes(analyze(PERK_PERK_ARGUMENT)) == ["CE2001"]


def test_an_instantiation_the_program_never_names_is_no_fault(analyze):
    """An unused implementation is dead code, which the extension path always allowed."""
    assert _codes(analyze(EXTENSION_UNNAMED_INSTANTIATION)) == []
    assert _codes(analyze(PERK_UNNAMED_INSTANTIATION)) == []


def test_a_template_target_still_binds_its_parameter(analyze):
    """The bare undeclared name is the TEMPLATE spelling and stays legal (#393)."""
    assert _codes(analyze(TEMPLATE_TARGET)) == []


def test_a_base_that_names_no_generic_is_one_ce2001_on_both_paths(analyze):
    """The base is the rest of the target, and the extension path read it not at all."""
    assert _codes(analyze(EXTENSION_UNKNOWN_BASE)) == ["CE2001"]
    assert _codes(analyze(PERK_UNKNOWN_BASE)) == ["CE2001"]
