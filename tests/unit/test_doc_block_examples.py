"""`- Example:` blocks: the prose/fence partition, and the structure it fills.

`docs/design/documentation.md` section 10 is the authority. R13 splits the block into
prose regions and fenced regions BEFORE the tags are read, R14 gives an example a
structure of its own, and R15 allows many of them.

The four rules asserted here are the four defects the phase-3 parse had: a tag-shaped
line inside example code became a tag, a blank line inside a fence truncated the
example, the fold that joins a tag's continuation lines destroyed the indentation, and
an unterminated fence disappeared without a signal.
"""
from __future__ import annotations

from lark import Token

from sushi_lang.semantics.ast_builder.declarations.docs import parse_doc_block


def _doc(text: str, line: int = 1, col: int = 1) -> Token:
    """A DOC_BLOCK token carrying `text`, spanned as the lexer would span it."""
    lines = text.split("\n")
    return Token(
        "DOC_BLOCK", text,
        line=line, column=col,
        end_line=line + len(lines) - 1,
        end_column=(len(lines[-1]) + 1) if len(lines) > 1 else col + len(text),
    )


def _block(*lines: str) -> Token:
    """A multi-line block, written one line per argument."""
    return _doc("##:\n" + "\n".join(lines) + "\n:##")


# -- what the fence carries -----------------------------------------------------

def test_a_fenced_example_is_captured_with_its_indentation():
    doc = parse_doc_block(_block(
        "Jumps through hyperspace.",
        "",
        "- Example:",
        "```sushi",
        "let i32 d = jump(3)??",
        "if (d > 2):",
        '    println("far")',
    ))
    assert len(doc.examples) == 1
    assert doc.examples[0].code == (
        "let i32 d = jump(3)??\n"
        "if (d > 2):\n"
        '    println("far")'
    )


def test_the_fence_lines_are_not_in_the_code():
    doc = parse_doc_block(_block(
        "- Example:",
        "```sushi",
        "let i32 d = 1",
        "```",
    ))
    assert "```" not in doc.examples[0].code
    assert doc.examples[0].code == "let i32 d = 1"


def test_the_info_string_lands_in_attrs_as_written():
    doc = parse_doc_block(_block(
        "- Example:",
        "```sushi no_run",
        "let i32 d = 1",
        "```",
    ))
    assert doc.examples[0].attrs == "sushi no_run"


def test_a_fence_indented_under_its_list_item_loses_that_indent():
    """CommonMark's own rule for a fenced block, and the reason it is applied here."""
    doc = parse_doc_block(_block(
        "- Example:",
        "  ```sushi",
        "  if (true):",
        '      println("in")',
        "  ```",
    ))
    assert doc.examples[0].code == 'if (true):\n    println("in")'


def test_many_examples_are_many_entries_in_source_order():
    doc = parse_doc_block(_block(
        "- Example: the first one.",
        "```sushi",
        "let i32 a = 1",
        "```",
        "- Example: the second one.",
        "```sushi",
        "let i32 b = 2",
        "```",
    ))
    assert [example.code for example in doc.examples] == ["let i32 a = 1", "let i32 b = 2"]
    assert [tag.text for tag in doc.tags if tag.kind == "example"] == \
        ["the first one.", "the second one."]


# -- the partition (R13) --------------------------------------------------------

def test_a_tag_shaped_line_inside_example_code_is_not_a_tag():
    doc = parse_doc_block(_block(
        "- Example:",
        "```sushi",
        '- Returns: this line is code, and prints itself.',
        "```",
        "- Returns: the real one.",
    ))
    assert [tag.kind for tag in doc.tags] == ["example", "returns"]
    assert [tag.text for tag in doc.tags if tag.kind == "returns"] == ["the real one."]
    assert "- Returns: this line is code" in doc.examples[0].code


def test_a_blank_line_inside_a_fence_does_not_truncate_the_example():
    doc = parse_doc_block(_block(
        "- Example:",
        "```sushi",
        "let i32 a = 1",
        "",
        "let i32 b = 2",
        "```",
    ))
    assert doc.examples[0].code == "let i32 a = 1\n\nlet i32 b = 2"


def test_the_example_tag_carries_its_caption_and_not_the_code():
    doc = parse_doc_block(_block(
        "- Example: a caption that",
        "  wraps onto a second line.",
        "```sushi",
        "let i32 a = 1",
        "```",
    ))
    example_tag = next(tag for tag in doc.tags if tag.kind == "example")
    assert example_tag.text == "a caption that\nwraps onto a second line."
    assert "let i32 a" not in example_tag.text


# -- the body (R1 still holds) --------------------------------------------------

def test_a_fence_introduced_by_no_tag_stays_prose_and_yields_no_example():
    doc = parse_doc_block(_block(
        "Reads a header.",
        "",
        "Some prose about it.",
        "```text",
        "not an example",
        "```",
        "- Returns: the header.",
    ))
    assert doc.examples == []
    assert doc.body == "Some prose about it.\n```text\nnot an example\n```"


def test_the_body_still_stops_at_the_first_tag():
    doc = parse_doc_block(_block(
        "Reads a header.",
        "",
        "The body.",
        "- Example:",
        "```sushi",
        "let i32 a = 1",
        "```",
        "Prose after the tags is not carried.",
    ))
    assert doc.summary == "Reads a header."
    assert doc.body == "The body."
    assert "```" not in doc.body
    assert doc.examples[0].code == "let i32 a = 1"


# -- the two defects (R14, reported by the pass as CE7007 and CE7008) -----------

def test_an_example_tag_with_no_fence_is_a_no_fence_defect():
    doc = parse_doc_block(_block(
        "Jumps.",
        "",
        "- Example: nothing follows this tag.",
    ))
    assert len(doc.examples) == 1
    assert doc.examples[0].defect == "no-fence"
    assert doc.examples[0].code == ""


def test_a_tag_followed_by_another_tag_is_a_no_fence_defect():
    doc = parse_doc_block(_block(
        "- Example: still nothing.",
        "- Returns: parsecs.",
    ))
    assert [example.defect for example in doc.examples] == ["no-fence"]


def test_a_fence_that_never_closes_is_an_unterminated_defect():
    doc = parse_doc_block(_block(
        "- Example:",
        "```sushi",
        "let i32 a = 1",
    ))
    assert len(doc.examples) == 1
    assert doc.examples[0].defect == "unterminated"
    assert doc.examples[0].code == "let i32 a = 1"


def test_a_sound_example_carries_no_defect():
    doc = parse_doc_block(_block(
        "- Example:",
        "```sushi",
        "let i32 a = 1",
        "```",
    ))
    assert doc.examples[0].defect is None


def test_the_defect_span_points_at_something_a_reader_can_fix():
    """A missing fence points at the tag; an unterminated one at the opening fence."""
    no_fence = parse_doc_block(_block("- Example: nothing follows."))
    assert no_fence.examples[0].loc is not None
    assert no_fence.examples[0].loc.line == 2

    unterminated = parse_doc_block(_block("- Example:", "```sushi", "let i32 a = 1"))
    assert unterminated.examples[0].loc is not None
    assert unterminated.examples[0].loc.line == 3
