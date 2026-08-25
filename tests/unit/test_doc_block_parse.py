"""The doc-block parser: dedenting, the summary split, and telling a typo from prose.

`docs/design/documentation.md` sections 2 and 3 are the authority. The rules asserted
here are the ones a reader of a doc block cannot see for themselves: what the common
indent is measured against, where the summary stops, and how far a misspelling may be
from a keyword before it stops being one.
"""
from __future__ import annotations

from lark import Token

from sushi_lang.semantics.ast_builder.declarations.docs import (
    TAG_KEYWORDS,
    parse_doc_block,
    suggest_tag,
)


def _doc(text: str, line: int = 1, col: int = 1) -> Token:
    """A DOC_BLOCK token carrying `text`, spanned as the lexer would span it."""
    lines = text.split("\n")
    return Token(
        "DOC_BLOCK", text,
        line=line, column=col,
        end_line=line + len(lines) - 1,
        end_column=(len(lines[-1]) + 1) if len(lines) > 1 else col + len(text),
    )


# -- dedenting ------------------------------------------------------------------

def test_one_liner_keeps_its_single_line():
    doc = parse_doc_block(_doc("##: A simple const :##"))
    assert doc.text == "A simple const"
    assert doc.summary == "A simple const"
    assert doc.tags == []


def test_flush_block_is_unchanged():
    doc = parse_doc_block(_doc("##:\nJumps through hyperspace.\n:##"))
    assert doc.text == "Jumps through hyperspace."


def test_common_indent_comes_off_and_relative_indent_stays():
    doc = parse_doc_block(_doc(
        "##:\n"
        "    Returns a constant.\n"
        "\n"
        "        let i32 n = seven()\n"
        "    :##"
    ))
    assert doc.text == "Returns a constant.\n\n    let i32 n = seven()"


def test_the_opening_line_is_held_out_of_the_common_prefix():
    """Text after `##:` carries no indent of its own, so it cannot set the prefix."""
    doc = parse_doc_block(_doc(
        "##: First line here.\n"
        "    Second line.\n"
        "    :##"
    ))
    assert doc.text == "First line here.\nSecond line."


def test_blank_lines_at_the_edges_are_dropped():
    doc = parse_doc_block(_doc("##:\n\n  Body.\n\n  :##"))
    assert doc.text == "Body."


def test_a_tab_indented_block_dedents_too():
    doc = parse_doc_block(_doc("##:\n\tOne.\n\tTwo.\n\t:##"))
    assert doc.text == "One.\nTwo."


# -- the summary split ----------------------------------------------------------

def test_summary_is_the_first_paragraph():
    doc = parse_doc_block(_doc(
        "##:\nJumps through hyperspace.\n\nThe drive needs a warm coil.\n:##"
    ))
    assert doc.summary == "Jumps through hyperspace."
    assert "warm coil" in doc.text


def test_a_wrapped_summary_keeps_both_lines():
    doc = parse_doc_block(_doc(
        "##:\nJumps through hyperspace,\nwhich takes a warm coil.\n\nMore prose.\n:##"
    ))
    assert doc.summary == "Jumps through hyperspace,\nwhich takes a warm coil."


def test_summary_stops_at_the_first_tag():
    doc = parse_doc_block(_doc(
        "##:\nJumps through hyperspace.\n- Returns: parsecs.\n:##"
    ))
    assert doc.summary == "Jumps through hyperspace."


def test_a_block_that_is_only_tags_has_an_empty_summary():
    doc = parse_doc_block(_doc("##:\n- Returns: parsecs.\n:##"))
    assert doc.summary == ""


# -- tag recognition ------------------------------------------------------------

def test_the_four_keywords_are_the_vocabulary():
    assert TAG_KEYWORDS == ("Parameter", "Returns", "Errors", "Example")


def test_a_parameter_tag_carries_its_name_and_text():
    doc = parse_doc_block(_doc("##:\n- Parameter a: The incoming argument.\n:##"))
    tag, = doc.tags
    assert (tag.kind, tag.name, tag.text) == ("parameter", "a", "The incoming argument.")


def test_returns_errors_and_example_carry_no_name():
    doc = parse_doc_block(_doc(
        "##:\n- Returns: parsecs.\n- Errors: a cold drive.\n- Example: see below.\n:##"
    ))
    assert [(t.kind, t.name) for t in doc.tags] == [
        ("returns", None), ("errors", None), ("example", None),
    ]


def test_a_tag_description_wraps_onto_the_next_line():
    doc = parse_doc_block(_doc(
        "##:\n- Parameter b: The second one,\n  described across two lines.\n:##"
    ))
    tag, = doc.tags
    assert tag.text == "The second one,\ndescribed across two lines."


def test_a_tag_carries_its_own_span():
    """CE7002 puts a caret on the tag, not on the block."""
    doc = parse_doc_block(_doc(
        "##:\nSummary.\n\n- Parameter a: one.\n- Parameter b: two.\n:##", line=10))
    first, second = doc.tags
    assert (first.loc.line, second.loc.line) == (13, 14)


def test_prose_bullets_are_not_tags():
    doc = parse_doc_block(_doc(
        "##:\n"
        "- Note: this is prose.\n"
        "- Deprecated: reserved, and prose until a later phase.\n"
        "- Traps: reserved as well.\n"
        "- see docs/ffi.md for the rest\n"
        ":##"
    ))
    assert doc.tags == []


def test_an_indented_bullet_inside_a_code_example_is_not_a_tag():
    doc = parse_doc_block(_doc(
        "##:\nSummary.\n\n    - Returns: this sits inside an indented example.\n:##"
    ))
    assert doc.tags == []


# -- telling a typo from prose --------------------------------------------------

def test_a_near_miss_becomes_an_unknown_tag():
    doc = parse_doc_block(_doc("##:\n- Retruns: parsecs.\n:##"))
    tag, = doc.tags
    assert (tag.kind, tag.word) == ("unknown", "Retruns")


def test_distance_one_is_caught():
    assert suggest_tag("Error") == "Errors"
    assert suggest_tag("Paramter") == "Parameter"


def test_distance_two_is_caught():
    assert suggest_tag("Retruns") == "Returns"
    assert suggest_tag("Paremter") == "Parameter"


def test_distance_three_is_not():
    """`Err` is three edits from `Errors` and further from every other keyword."""
    assert suggest_tag("Err") is None


def test_a_reserved_keyword_is_never_a_near_miss():
    assert suggest_tag("Deprecated") is None
    assert suggest_tag("Traps") is None


def test_prose_words_are_never_near_misses():
    for word in ("Note", "Notes", "See", "Warning", "Todo"):
        assert suggest_tag(word) is None, word
