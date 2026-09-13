"""Every declaration kind answers one `| E` channel rule, with one caret (#662, #663).

Four kinds write the channel -- a free function, an extension method, a perk contract
and a perk implementation -- and each one used to answer differently. A free function
said CE2001 AND CE2084 for a name that spells nothing, and pointed both carets at the
RETURN type beside the channel. An extension said CE2001 alone, and let a REAL struct
or primitive through. A perk contract said nothing at all.

The rule has two halves, and a fixture cannot pin either one: `EXPECT_ERROR_CODE` is a
substring, so it cannot say that ONE code came out, and a fixture reads no span. The
matrix is what keeps the four kinds in step.
"""
from __future__ import annotations

import pytest


DECLARATIONS = (
    "struct Bad:\n"
    "    i32 n\n"
    "\n"
    "enum Good:\n"
    "    One\n"
    "\n"
    "enum MyErr@(T):\n"
    "    Bad2(T)\n"
    "    Worse\n"
    "\n"
    "struct Holder:\n"
    "    i32 n\n"
    "\n"
)

MAIN = "fn main() i32:\n    return Result.Ok(0)\n"

# What one channel can name: a name that spells nothing, two real non-enums, a plain
# enum, a user's generic enum and a built-in WRAPPER, which is an enum and is still not
# an error vocabulary (#668).
ARMS = {
    "unknown": "NoSuchError",
    "struct": "Bad",
    "primitive": "i32",
    "wrapper": "Maybe@(i32)",
    "generic user": "MyErr@(i32)",
    "enum": "Good",
}

# The code SET each arm answers, in every kind. A name that spells nothing is CE2001 and
# nothing else: CE2001 already says everything a reader can act on, and CE2084 on top of
# it is one fault told twice.
EXPECTED = {
    "unknown": {"CE2001"},
    "struct": {"CE2084"},
    "primitive": {"CE2084"},
    # A user's generic enum IS an enum, and the explicit `Result@(T, E)` spelling of one
    # has always compiled, so the `| E` spelling is legal too (#668). The rule resolves a
    # written instantiation before it asks the kind, and the channel is collected for all
    # four kinds now.
    "generic user": set(),
    "enum": set(),
    # A built-in wrapper passes the enum test and is refused by its own rule: the Err arm
    # already means failure, so an absence or a second Result in it says nothing.
    "wrapper": {"CE2086"},
}

# One channel per kind, written so the type under test starts at a known column.
KINDS = {
    "function": (
        lambda arm: DECLARATIONS
        + "fn halved(i32 v) i32 | %s:\n    return Result.Ok(v)\n\n" % arm
        + MAIN,
        # `fn halved(i32 v) i32 | ` is 23 characters.
        24,
    ),
    "extension": (
        lambda arm: DECLARATIONS
        + "extend Holder take(peek self) i32 | %s:\n    return self.n\n\n" % arm
        + MAIN,
        37,
    ),
    "perk contract": (
        lambda arm: DECLARATIONS
        + "perk Source:\n    fn read_one(peek self) i32 | %s\n\n" % arm
        + MAIN,
        34,
    ),
    "perk implementation": (
        lambda arm: DECLARATIONS
        + "perk Source:\n    fn read_one(peek self) i32 | %s\n\n" % arm
        + "extend Holder with Source:\n"
        + "    fn read_one(peek self) i32 | %s:\n        return self.n\n\n" % arm
        + MAIN,
        34,
    ),
}


def _codes(reporter) -> set:
    return {item.code for item in reporter.items}


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("arm", sorted(ARMS))
def test_every_kind_answers_one_channel_rule(analyze, kind: str, arm: str) -> None:
    """The code SET, which is what tells one fault from one fault told twice."""
    source, _column = KINDS[kind]
    assert _codes(analyze(source(ARMS[arm]))) == EXPECTED[arm], (kind, arm)


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("arm", ["unknown", "struct", "primitive", "wrapper"])
def test_every_caret_points_at_the_channel(analyze, kind: str, arm: str) -> None:
    """The caret marks the channel, and never the return type beside it."""
    source, column = KINDS[kind]
    reported = [item for item in analyze(source(ARMS[arm])).items]

    assert reported, (kind, arm)
    for item in reported:
        assert item.span is not None, (kind, arm)
        assert item.span.col == column, (kind, arm, item.span)
        assert item.span.end_col - item.span.col == len(ARMS[arm]), (kind, arm)


# `T | E` is SUGAR for `Result@(T, E)`, so the two spellings must admit exactly the same
# `E`. They did not: CE2084 was a rule on the SHORT form alone, and a struct error
# compiled in the long form while the short form refused it (#668). One derivation --
# `signature_result_arms` -- answers the arm for both now, so this pins the equality
# rather than either side's answer.
def LONG(arm: str) -> str:
    """The same signature, written `Result@(i32, E)` instead of `i32 | E`."""
    return (DECLARATIONS
            + "fn halved(i32 v) Result@(i32, %s):\n    return Result.Ok(v)\n\n" % arm
            + MAIN)


SHORT = KINDS["function"][0]


@pytest.mark.parametrize("arm", sorted(ARMS))
def test_the_two_spellings_admit_the_same_error_type(analyze, arm: str) -> None:
    """`T | E` and `Result@(T, E)` answer alike, or the short form is not sugar."""
    short = _codes(analyze(SHORT(ARMS[arm])))
    long_form = _codes(analyze(LONG(ARMS[arm])))

    # A name that spells nothing is the one row where the COUNT may differ: the long
    # form's return walk adds its own complaints about the Result it could not build.
    # The channel rule stays silent in both, which is what this asserts.
    if arm == "unknown":
        assert "CE2001" in short and "CE2001" in long_form, arm
        assert not ({"CE2084", "CE2086"} & (short | long_form)), arm
        return

    assert short == long_form, (arm, short, long_form)
