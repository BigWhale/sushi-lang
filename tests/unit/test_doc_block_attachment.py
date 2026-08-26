"""Attachment: which node a doc block lands on, and the promise that none vanishes.

`docs/design/documentation.md` section 5 is the authority. A doc block that is written
and then silently dropped is the failure the whole feature exists to remove, so the
gate here is the TOTALITY -- every block ends up attached, lifted, or in
`Program.orphan_docs` -- rather than any one of the three outcomes.
"""
from __future__ import annotations

import dataclasses
from typing import List

import pytest

from sushi_lang.internals.parser import build_parser
from sushi_lang.semantics.ast import DocBlock, Program
from sushi_lang.semantics.ast_builder import ASTBuilder

_PARSER = None


def _program(src: str) -> Program:
    global _PARSER
    if _PARSER is None:
        _PARSER = build_parser()
    return ASTBuilder().build(_PARSER.parse(src))


def _reachable_docs(node, seen=None, out=None) -> List[DocBlock]:
    """Every DocBlock reachable from `node`, once each, wherever it is parked."""
    if seen is None:
        seen, out = set(), []
    if id(node) in seen:
        return out
    seen.add(id(node))

    if isinstance(node, DocBlock):
        out.append(node)
        return out
    if isinstance(node, (list, tuple)):
        for item in node:
            _reachable_docs(item, seen, out)
        return out
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        for field in dataclasses.fields(node):
            _reachable_docs(getattr(node, field.name), seen, out)
    return out


MAIN = "fn main() i32:\n    return Result.Ok(0)\n"


# -- the three positions --------------------------------------------------------

def test_a_block_attaches_to_the_declaration_on_the_next_line():
    program = _program("##: The answer. :##\nconst i32 ANSWER = 42\n" + MAIN)
    const, = program.constants
    assert const.doc is not None
    assert const.doc.summary == "The answer."
    assert program.orphan_docs == []


def test_the_first_block_in_a_file_that_attaches_to_nothing_is_the_unit_block():
    program = _program("##: Unit docs. :##\n\n" + MAIN)
    assert program.doc is not None
    assert program.doc.summary == "Unit docs."
    assert program.orphan_docs == []


def test_a_body_first_block_lifts_onto_the_enclosing_function():
    program = _program(
        "fn probe() i32:\n"
        "    ##: Documented from inside its own body. :##\n"
        "    return Result.Ok(7)\n" + MAIN
    )
    probe = next(f for f in program.functions if f.name == "probe")
    assert probe.doc is not None
    assert probe.doc is probe.body.doc
    assert program.orphan_docs == []


def test_a_body_first_block_lifts_onto_an_extension_too():
    program = _program(
        "extend i32 squared() i32:\n"
        "    ##: Multiplies the receiver by itself. :##\n"
        "    return self * self\n" + MAIN
    )
    squared, = program.extensions
    assert squared.doc is not None
    assert squared.doc is squared.body.doc


# -- what breaks attachment -----------------------------------------------------

def test_a_blank_line_breaks_the_attachment():
    program = _program(
        "##: The answer. :##\nconst i32 ANSWER = 42\n\n"
        "##: Detached. :##\n\nconst i32 OTHER = 7\n" + MAIN
    )
    detached, = program.orphan_docs
    assert detached.summary == "Detached."
    assert detached.orphan_reason == "detached"


def test_a_comment_line_breaks_the_attachment():
    """`_NEWLINE` absorbs a comment line, so the builder sees only the line gap."""
    program = _program(
        "##: The answer. :##\nconst i32 ANSWER = 42\n\n"
        "##: Detached by the comment below. :##\n"
        "# an ordinary comment\n"
        "const i32 OTHER = 7\n" + MAIN
    )
    detached, = program.orphan_docs
    assert detached.orphan_reason == "detached"


def test_a_block_that_is_not_first_in_a_body_is_a_different_orphan():
    program = _program(
        "fn probe() i32:\n"
        "    let i32 n = 7\n"
        "    ##: Not the first item here. :##\n"
        "    return Result.Ok(n)\n" + MAIN
    )
    stray, = program.orphan_docs
    assert stray.orphan_reason == "in-body"


def test_a_block_in_a_body_that_takes_no_docs_is_an_orphan():
    """A lambda body and an `if` arm have no declaration to lift onto."""
    program = _program(
        "fn probe() i32:\n"
        "    if (true):\n"
        "        ##: An `if` arm takes no docs. :##\n"
        "        return Result.Ok(7)\n"
        "    return Result.Ok(0)\n" + MAIN
    )
    stray, = program.orphan_docs
    assert stray.orphan_reason == "detached"


def test_a_declaration_documented_twice_keeps_both_blocks():
    """CE7006 is relational, so the pass needs a span for each block."""
    program = _program(
        "##: From above. :##\n"
        "fn probe() i32:\n"
        "    ##: And from inside. :##\n"
        "    return Result.Ok(7)\n" + MAIN
    )
    probe = next(f for f in program.functions if f.name == "probe")
    assert probe.doc.summary == "From above."
    assert probe.body.doc.summary == "And from inside."
    assert probe.doc is not probe.body.doc


# -- member positions -----------------------------------------------------------

def test_a_member_block_attaches_to_the_member_below_it():
    program = _program(
        "struct Point:\n"
        "    ##: The horizontal offset. :##\n"
        "    i32 x\n"
        "    i32 y\n"
        "enum Drive:\n"
        "    ##: The coil is cold. :##\n"
        "    Cold\n"
        "    Ready\n" + MAIN
    )
    point, = program.structs
    drive, = program.enums
    assert point.fields[0].doc is not None and point.fields[1].doc is None
    assert drive.variants[0].doc is not None and drive.variants[1].doc is None


# -- the totality gate ----------------------------------------------------------

TOTALITY_SOURCES = [
    pytest.param("##: Unit docs. :##\n\n" + MAIN, id="unit"),
    pytest.param("##: The answer. :##\nconst i32 ANSWER = 42\n" + MAIN, id="const"),
    pytest.param(
        "##: Unit docs. :##\n\n"
        "##: Detached. :##\n\n"
        "##: The answer. :##\n"
        "const i32 ANSWER = 42\n" + MAIN,
        id="three-top-level",
    ),
    pytest.param(
        "##: From above. :##\n"
        "fn probe() i32:\n"
        "    ##: And from inside. :##\n"
        "    let i32 n = 7\n"
        "    ##: And a stray one. :##\n"
        "    return Result.Ok(n)\n" + MAIN,
        id="body",
    ),
    pytest.param(
        "##: A point. :##\n"
        "struct Point:\n"
        "    ##: The horizontal offset. :##\n"
        "    i32 x\n"
        "    ##: The vertical offset. :##\n"
        "    i32 y\n" + MAIN,
        id="struct-members",
    ),
    pytest.param(
        "##: Anything nameable. :##\n"
        "perk Named:\n"
        "    ##: The name. :##\n"
        "    fn name() string\n"
        "##: A point names itself. :##\n"
        "extend Point with Named:\n"
        "    ##: The implementation. :##\n"
        "    fn name() string:\n"
        "        return \"point\"\n"
        "struct Point:\n"
        "    i32 x\n" + MAIN,
        id="perk-and-impl",
    ),
    pytest.param(
        "##: Foreign declarations. :##\n"
        "unsafe external \"C\" as libc because \"the totality sweep\":\n"
        "    ##: The length in bytes. :##\n"
        "    fn strlen(string s) i64 = \"strlen\"\n" + MAIN,
        id="external",
    ),
]


@pytest.mark.parametrize("src", TOTALITY_SOURCES)
def test_no_doc_block_vanishes(src: str):
    """Every `##:` written reaches the AST exactly once. This is the gate."""
    written = src.count("##:")
    program = _program(src)
    reached = _reachable_docs(program)

    assert len(reached) == written, (
        f"{written} doc blocks written, {len(reached)} reached the AST"
    )
    assert len({id(doc) for doc in reached}) == len(reached), "a block was reached twice"
