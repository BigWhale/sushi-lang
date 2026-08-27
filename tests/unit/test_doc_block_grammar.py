"""The grammar side of doc blocks: the corpus regression and the three lex-time errors.

`docs/design/documentation.md` section 4 measured two things against the real grammar
before the terminals were written: that no `.sushi` file in the tree contains `:#` in
any position or a line-initial `##`, and that narrowing `_NEWLINE` leaves ordinary
comments alone. This module is where those measurements live permanently.

The delimiter diagnostics are asserted here rather than beside the parser because they
are decided at LEX time, before the AST builder has an opinion about anything.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.parser import build_parser, parse_to_ast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__sushi_cache__", ".git", "node_modules", ".venv", "venv", "build", "dist"}

# Named roots rather than the repository root, for the reason
# `test_path_references_exist.py` gives for the same choice: the top level holds
# local-only scratch files (`/a.sushi` is in `.gitignore`), and a scratch file is not
# a corpus this gate has any business measuring.
SCAN_ROOTS = ("docs", "editor-support", "site", "sushi_lang", "tests", "toolchain")

# What writes doc blocks ON PURPOSE. Everything else in the tree predates the feature
# and must not change meaning, which is what this gate measures. Each entry is a path
# prefix and each one is deliberate:
#   tests/docs                        -- the feature's own corpus
#   tests/libs/helpers/doc_lib.sushi  -- phase 3's documented helper library. It has to
#                                        live beside the other helpers, because
#                                        `build_test_helpers` globs that directory.
DOC_SOURCES = ("tests/docs", "tests/libs/helpers/doc_lib.sushi")

# `.sushi` files that do not parse, and did not before doc blocks existed. Each entry
# carries its reason; adding one is deliberate. A `test_err_` file declaring a CE6xxx
# code is exempt by its own header and is not listed here.
PARSE_EXEMPT: set[str] = set()

MAIN = "fn main() i32:\n    return Result.Ok(0)\n"


def _sushi_files() -> list[Path]:
    found: list[Path] = []
    for root in SCAN_ROOTS:
        for path in sorted((PROJECT_ROOT / root).rglob("*.sushi")):
            if not any(part in SKIP_DIRS for part in path.relative_to(PROJECT_ROOT).parts):
                found.append(path)
    return found


def _expects_syntax_error(text: str) -> bool:
    return any(code.startswith("CE60")
               for code in re.findall(r"EXPECT_ERROR_CODE:\s*(\w+)", text))


# -- the corpus regression ------------------------------------------------------

def test_no_doc_delimiters_outside_the_doc_tests():
    """Zero `:#` and zero line-initial `##`: no existing source changes meaning."""
    offenders: list[str] = []
    for path in _sushi_files():
        rel = str(path.relative_to(PROJECT_ROOT))
        if rel.startswith(DOC_SOURCES):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if ":#" in line or line.lstrip().startswith("##"):
                offenders.append(f"{rel}:{number}: {line.strip()}")

    assert not offenders, (
        "doc-block delimiters outside " + str(DOC_SOURCES) + " -- these sources changed "
        "meaning when the terminals landed:\n  " + "\n  ".join(offenders)
    )


def test_the_corpus_still_parses():
    """Every `.sushi` file in the tree parses, bar the deliberate syntax-error tests."""
    parser = build_parser()
    broken: list[str] = []
    for path in _sushi_files():
        rel = str(path.relative_to(PROJECT_ROOT))
        if rel in PARSE_EXEMPT:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _expects_syntax_error(text):
            continue
        try:
            parser.parse(text)
        except Exception as exc:  # noqa: BLE001 -- the failure itself is the finding
            broken.append(f"{rel}: {type(exc).__name__}: {exc}".split("\n")[0])

    assert not broken, "files that no longer parse:\n  " + "\n  ".join(broken)


# -- ordinary comments still behave ---------------------------------------------

@pytest.mark.parametrize("comment", ["# one hash", "## two hashes", "### three hashes"])
def test_a_full_line_comment_is_still_absorbed(comment: str):
    program, _ = parse_to_ast(f"{comment}\nconst i32 ANSWER = 42\n{MAIN}")
    assert [c.name for c in program.constants] == ["ANSWER"]


@pytest.mark.parametrize("comment", ["# one hash", "## two hashes", "### three hashes"])
def test_a_trailing_comment_is_still_absorbed(comment: str):
    program, _ = parse_to_ast(f"const i32 ANSWER = 42  {comment}\n{MAIN}")
    assert [c.name for c in program.constants] == ["ANSWER"]


def test_a_run_of_comment_lines_is_still_one_newline():
    program, _ = parse_to_ast(
        "# one\n# two\n### three\nconst i32 ANSWER = 42\n" + MAIN)
    assert [c.name for c in program.constants] == ["ANSWER"]


# -- the block is one token -----------------------------------------------------

def test_a_doc_block_lexes_as_a_single_token():
    parser = build_parser()
    tokens = [t for t in parser.lex("##:\nline one\nline two\n:##\n" + MAIN)
              if t.type == "DOC_BLOCK"]
    assert len(tokens) == 1
    assert tokens[0].value.startswith("##:") and tokens[0].value.endswith(":##")


def test_a_closer_inside_a_string_does_not_end_a_block():
    """The line-initial rule keeps the match out of a string literal."""
    program, _ = parse_to_ast(
        "##:\nA block.\n:##\n"
        "fn main() i32:\n"
        "    let string s = \"a :## b\"\n"
        "    println(s)\n"
        "    return Result.Ok(0)\n"
    )
    assert program.doc is None  # it attaches to `fn main`, it is not unit docs


# -- the three lex-time diagnostics ---------------------------------------------

def _fails(src: str) -> SyntaxDiagnostic:
    with pytest.raises(SyntaxDiagnostic) as caught:
        parse_to_ast(src)
    return caught.value


def test_an_unclosed_block_is_ce6011_at_the_opener():
    exc = _fails("##: opened and never closed\n\n" + MAIN)
    assert exc.code == "CE6011"
    assert (exc.span.line, exc.span.col) == (1, 1)


def test_the_runaway_from_section_two_is_ce6011():
    """A lazy delimiter would swallow the constant and report nothing at all."""
    exc = _fails("##: docs for x\nconst i32 x = 1\n\n##: docs for y :##\n")
    assert exc.code == "CE6011"
    assert (exc.span.line, exc.span.col) == (1, 1)


def test_an_opener_inside_a_body_is_ce6011_where_it_stands():
    exc = _fails("fn main() i32:\n    ##: opened here\n    return Result.Ok(0)\n")
    assert exc.code == "CE6011"
    assert (exc.span.line, exc.span.col) == (2, 5)


def test_a_stray_closer_is_ce6012():
    exc = _fails(":##\n" + MAIN)
    assert exc.code == "CE6012"
    assert (exc.span.line, exc.span.col) == (1, 1)


def test_a_stray_closer_inside_a_body_is_ce6012_where_it_stands():
    exc = _fails("fn main() i32:\n    :##\n    return Result.Ok(0)\n")
    assert exc.code == "CE6012"
    assert (exc.span.line, exc.span.col) == (2, 5)


def test_a_line_initial_opener_inside_a_block_is_ce6013():
    exc = _fails("##:\nouter\n##: inner\n:##\n" + MAIN)
    assert exc.code == "CE6013"
    assert (exc.span.line, exc.span.col) == (3, 1)


def test_ce6013_is_relational():
    """The block that is broken is the OUTER one, and the note is what says so."""
    exc = _fails("##:\nouter\n##: inner\n:##\n" + MAIN)
    assert exc.notes, "CE6013 rendered with a single location is a regression"
    _message, span, _filename = exc.notes[0]
    assert (span.line, span.col) == (1, 1)


def test_an_indented_inner_opener_still_counts():
    exc = _fails("##:\nouter\n    ##: inner\n:##\n" + MAIN)
    assert exc.code == "CE6013"


def test_a_clean_block_raises_nothing():
    for src in ("##: one liner :##\n" + MAIN,
                "##:\nmulti\nline\n:##\n" + MAIN,
                "##: opening line\nand a second\n:##\n" + MAIN):
        parse_to_ast(src)
