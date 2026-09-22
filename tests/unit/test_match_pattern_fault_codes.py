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

_MAIN = "\nfn main() i32:\n    return Result.Ok(0)\n"


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def _errors(reporter) -> list[str]:
    return [item.code for item in reporter.items if item.kind == "error"]


# --- CE2048: the scrutinee itself ------------------------------------------------


def test_a_string_scrutinee_reads_the_scrutinee_code(analyze):
    source = (
        "fn main() i32:\n"
        "    let string s = \"x\"\n"
        "    match s:\n"
        "        _ -> println(\"a\")\n"
        "    return Result.Ok(0)\n"
    )
    assert _codes(analyze(source, name="m")) == ["CE2048"]


def test_the_scrutinee_code_quotes_a_type_and_nothing_else(analyze):
    source = (
        "fn main() i32:\n"
        "    let string s = \"x\"\n"
        "    match s:\n"
        "        _ -> println(\"a\")\n"
        "    return Result.Ok(0)\n"
    )
    message = analyze(source, name="m").items[0].message
    assert message.endswith("got 'string'"), message


# --- CE2107: a pattern names another enum ----------------------------------------


_TWO_ENUMS = (
    "enum Shape:\n"
    "    Circle(i32)\n"
    "    Square(i32)\n"
    "\n"
    "enum Other:\n"
    "    Alpha\n"
    "\n"
)


def _arm_names_another_enum() -> str:
    return (
        _TWO_ENUMS
        + "fn main() i32:\n"
        "    let Shape s = Shape.Circle(2)\n"
        "    match s:\n"
        "        Other.Alpha -> println(\"a\")\n"
        "        _ -> println(\"b\")\n"
        "    return Result.Ok(0)\n"
    )


def test_an_arm_that_names_another_enum_reads_its_own_code(analyze):
    assert _codes(analyze(_arm_names_another_enum(), name="m")) == ["CE2107"]


def test_the_arm_code_never_says_the_enum_is_not_an_enum(analyze):
    """`Other` IS an enum. The old text said it was not."""
    message = analyze(_arm_names_another_enum(), name="m").items[0].message
    assert "must be an enum" not in message, message
    assert "'Other'" in message and "'Shape'" in message, message


def test_the_arm_code_is_relational(analyze):
    """Tier 3: the note carries its OWN location."""
    item = analyze(_arm_names_another_enum(), name="m").items[0]
    notes = [sub for sub in item.sub if sub.kind == "note"]
    assert len(notes) == 1, item.sub
    assert notes[0].span is not None, notes[0]
    # The note points at the scrutinee, not at the arm.
    assert notes[0].span.line != item.span.line


def _nested_pattern_names_another_enum() -> str:
    return (
        "enum Inner:\n"
        "    One\n"
        "\n"
        "enum Other:\n"
        "    Alpha\n"
        "\n"
        "enum Outer:\n"
        "    Wrap(Inner)\n"
        "\n"
        "fn main() i32:\n"
        "    let Outer o = Outer.Wrap(Inner.One)\n"
        "    match o:\n"
        "        Outer.Wrap(Other.Alpha) -> println(\"a\")\n"
        "    return Result.Ok(0)\n"
    )


def test_a_nested_pattern_that_names_another_enum_reads_the_arm_code(analyze):
    assert _codes(analyze(_nested_pattern_names_another_enum(), name="m")) == ["CE2107"]


def test_the_nested_arm_code_is_relational(analyze):
    item = analyze(_nested_pattern_names_another_enum(), name="m").items[0]
    notes = [sub for sub in item.sub if sub.kind == "note"]
    assert len(notes) == 1, item.sub
    assert notes[0].span is not None, notes[0]


# --- CE2108: a nested pattern over a value that is not an enum -------------------


def test_a_nested_pattern_over_an_integer_payload_reads_the_nested_code(analyze):
    source = (
        "enum Box:\n"
        "    Held(i32)\n"
        "\n"
        "enum Other:\n"
        "    Alpha\n"
        "\n"
        "fn main() i32:\n"
        "    let Box b = Box.Held(3)\n"
        "    match b:\n"
        "        Box.Held(Other.Alpha) -> println(\"a\")\n"
        "    return Result.Ok(0)\n"
    )
    assert _codes(analyze(source, name="m")) == ["CE2108"]


def test_a_nested_pattern_inside_own_reads_the_nested_code(analyze):
    source = (
        "enum Node:\n"
        "    Leaf(Own@(i32))\n"
        "\n"
        "enum Other:\n"
        "    Alpha\n"
        "\n"
        "fn main() i32:\n"
        "    let Node n = Node.Leaf(Own.alloc(3))\n"
        "    match n:\n"
        "        Node.Leaf(Own(Other.Alpha)) -> println(\"a\")\n"
        "    return Result.Ok(0)\n"
    )
    assert _codes(analyze(source, name="m")) == ["CE2108"]


def test_the_nested_code_quotes_a_type_and_nothing_else(analyze):
    source = (
        "enum Box:\n"
        "    Held(i32)\n"
        "\n"
        "enum Other:\n"
        "    Alpha\n"
        "\n"
        "fn main() i32:\n"
        "    let Box b = Box.Held(3)\n"
        "    match b:\n"
        "        Box.Held(Other.Alpha) -> println(\"a\")\n"
        "    return Result.Ok(0)\n"
    )
    message = analyze(source, name="m").items[0].message
    assert message.endswith("got 'i32'"), message


# --- CE2109: an Own(...) pattern over a value that is not an Own@(T) -------------


def test_an_own_pattern_over_an_integer_payload_reads_the_own_code(analyze):
    source = (
        "enum Box:\n"
        "    Held(i32)\n"
        "\n"
        "fn main() i32:\n"
        "    let Box b = Box.Held(3)\n"
        "    match b:\n"
        "        Box.Held(Own(_)) -> println(\"own\")\n"
        "    return Result.Ok(0)\n"
    )
    assert _errors(analyze(source, name="m")) == ["CE2109"]


def test_the_own_code_quotes_a_type_and_nothing_else(analyze):
    source = (
        "enum Box:\n"
        "    Held(i32)\n"
        "\n"
        "fn main() i32:\n"
        "    let Box b = Box.Held(3)\n"
        "    match b:\n"
        "        Box.Held(Own(_)) -> println(\"own\")\n"
        "    return Result.Ok(0)\n"
    )
    message = [i for i in analyze(source, name="m").items
               if i.kind == "error"][0].message
    assert message.endswith("got 'i32'"), message


def test_an_own_type_with_no_readable_payload_reads_the_own_code(analyze):
    """`Own@(i32, i32)` is not an `Own@(T)`. It reaches here behind a CE2001 cascade."""
    source = (
        "enum Node:\n"
        "    Leaf(Own@(i32, i32))\n"
        "\n"
        "enum Other:\n"
        "    Alpha\n"
        "\n"
        "fn main() i32:\n"
        "    let Node n = Node.Leaf(Own.alloc(3))\n"
        "    match n:\n"
        "        Node.Leaf(Own(Other.Alpha)) -> println(\"a\")\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(source, name="m"))
    assert "CE2109" in codes, codes
    assert "CE2048" not in codes, codes


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
