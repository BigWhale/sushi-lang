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
    "struct Holder:\n"
    "    i32 n\n"
    "\n"
)

MAIN = "fn main() i32:\n    return Result.Ok(0)\n"

# What one channel can name: a name that spells nothing, two real non-enums, an
# instantiation nothing collected, and an enum.
ARMS = {
    "unknown": "NoSuchError",
    "struct": "Bad",
    "primitive": "i32",
    "generic": "Maybe@(i32)",
    "enum": "Good",
}

# The code SET each arm answers, in every kind. A name that spells nothing is CE2001 and
# nothing else: CE2001 already says everything a reader can act on, and CE2084 on top of
# it is one fault told twice.
EXPECTED = {
    "unknown": {"CE2001"},
    "struct": {"CE2084"},
    "primitive": {"CE2084"},
    # An instantiation a channel names is collected from a FUNCTION signature and from
    # no other kind, so the name is real there and spells nothing everywhere else. The
    # asymmetry belongs to instantiation collection, and is measured here, not fixed.
    "generic": {"CE2001"},
    "enum": set(),
}

OVERRIDE = {("generic", "function"): {"CE2084"}}

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
    expected = OVERRIDE.get((arm, kind), EXPECTED[arm])
    assert _codes(analyze(source(ARMS[arm]))) == expected, (kind, arm)


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("arm", ["unknown", "struct", "primitive", "generic"])
def test_every_caret_points_at_the_channel(analyze, kind: str, arm: str) -> None:
    """The caret marks the channel, and never the return type beside it."""
    source, column = KINDS[kind]
    reported = [item for item in analyze(source(ARMS[arm])).items]

    assert reported, (kind, arm)
    for item in reported:
        assert item.span is not None, (kind, arm)
        assert item.span.col == column, (kind, arm, item.span)
        assert item.span.end_col - item.span.col == len(ARMS[arm]), (kind, arm)
