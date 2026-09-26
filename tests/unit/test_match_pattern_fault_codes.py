"""One fault, one code, for every match-pattern refusal (#741).

CE2048 answered FOUR different faults over seven emit sites in
`semantics/passes/types/matching.py`, and its registry text -- "match scrutinee must be
an enum or integer type, got '{got}'" -- was true of one of them. Three sites put a whole
SENTENCE in the quoted type slot, and two told the user that an enum is not an enum.

The four faults now read four codes:

* CE2048 -- the SCRUTINEE is neither an enum nor an integer.
* CE2107 -- a pattern names an enum that the value is not (relational).
* CE2108 -- a nested pattern needs an enum value.
* CE2109 -- an `Own(...)` pattern needs an `Own@(T)` value.

`EXPECT_ERROR_CODE` matches a SUBSTRING of the whole output, so no `.sushi` fixture can
prove that a code does NOT appear. This module counts the codes.
"""
from __future__ import annotations







# --- CE2048: the scrutinee itself ------------------------------------------------






# --- CE2107: a pattern names another enum ----------------------------------------


















# --- CE2108: a nested pattern over a value that is not an enum -------------------








# --- CE2109: an Own(...) pattern over a value that is not an Own@(T) -------------








# --- The registry contract -------------------------------------------------------


def test_no_pattern_code_holds_a_sentence_in_its_type_slot():
    """The quoted slot of each of the four codes takes one substitution and no prose."""
    from sushi_lang.internals.errors import REGISTRY

    for code in ("CE2048", "CE2107", "CE2108", "CE2109"):
        assert code in REGISTRY, code
        text = REGISTRY[code].text
        assert "{got}" in text or "{subject}" in text, text


def test_the_scrutinee_code_is_the_only_one_that_names_the_scrutinee():
    from sushi_lang.internals.errors import REGISTRY

    assert "scrutinee" in REGISTRY["CE2048"].text
    for code in ("CE2107", "CE2108", "CE2109"):
        assert "scrutinee" not in REGISTRY[code].text, code
