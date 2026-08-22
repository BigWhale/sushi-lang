"""Typecheck-pass validation for type-pack calls."""
from __future__ import annotations

# --- A valid pack call: every element implements the constraint perk ---------

_VALID = """\
perk Display:
    fn display() string

extend i32 with Display:
    fn display() string:
        return Result.Ok("int")

extend string with Display:
    fn display() string:
        return Result.Ok(self)

fn print_all@(...Ts: Display)(...Ts args) ~:
    return Result.Ok(~)

fn main() i32:
    print_all(42, "hi")
    return Result.Ok(0)
"""

# --- A constraint-violating pack call: 'string' does not implement Display ----

_VIOLATION = """\
perk Display:
    fn display() string

extend i32 with Display:
    fn display() string:
        return Result.Ok("int")

fn print_all@(...Ts: Display)(...Ts args) ~:
    return Result.Ok(~)

fn main() i32:
    print_all(42, "nope")
    return Result.Ok(0)
"""


def _codes(reporter):
    return [item.code for item in reporter.items]


def test_valid_pack_call_typechecks(analyze):
    """A valid pack call resolves to the monomorphized symbol: no inference or constraint
    diagnostics (CW1001 unused-variable warnings are expected since the trivial body does not
    yet `expand` the pack -- that is T7).
    """
    reporter = analyze(_VALID)
    codes = _codes(reporter)
    assert "CE2060" not in codes  # inference must succeed
    assert "CE2061" not in codes  # mangled symbol must be found
    assert "CE2090" not in codes  # all elements satisfy Display


def test_pack_constraint_violation_emits_ce2090(analyze):
    """Each pack element must satisfy every perk constraint; a non-implementing element type
    triggers CE2090 with its 0-based position within the pack.
    """
    reporter = analyze(_VIOLATION)
    codes = _codes(reporter)
    assert "CE2090" in codes
    # No spurious inference failure: the pack still resolved its type-args.
    assert "CE2060" not in codes

    ce2090 = [it for it in reporter.items if it.code == "CE2090"]
    assert len(ce2090) == 1
    msg = ce2090[0].message
    # element 1 == the 'string' argument ("nope"); element 0 (i32) is fine.
    assert "element 1" in msg
    assert "string" in msg
    assert "Display" in msg
