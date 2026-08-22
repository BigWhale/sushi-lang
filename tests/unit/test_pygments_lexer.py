"""The documentation highlighter must know the language the compiler accepts.

The lexer is not on the compiler's path, so nothing failed when the language moved
under it. Its last real refresh targeted 0.10.0 syntax; the `@(...)` generic form
of 0.11.0 and the literal underscores of 0.11.1 both landed with no change here,
and the docstring version was bumped twice over a lexer that had not been read.

Three gates, each one a way that drift stayed invisible:
  - the grammar's keywords must all be known to the lexer,
  - every numeric shape the grammar accepts must lex as ONE token,
  - no character in the corpus may fall through to the catch-all rule.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from pygments.token import Number, Text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEXER_PKG = PROJECT_ROOT / "docs" / "sushi-pygments"
GRAMMAR = PROJECT_ROOT / "sushi_lang" / "grammar.lark"

sys.path.insert(0, str(LEXER_PKG))
import sushi_pygments  # noqa: E402

lexer = sushi_pygments.SushiLexer()

# Grammar words that are deliberately NOT lexer keywords.
#   lib -- a path segment inside `use <lib/name>`, an ordinary identifier anywhere else.
_NOT_KEYWORDS = {"lib"}


def _lexer_vocabulary() -> set[str]:
    """Every word the lexer knows, from every word list the module defines.

    Discovered rather than enumerated, so a new category of word joins the gate
    without anyone remembering to add it here.
    """
    vocabulary: set[str] = set()
    for name, value in vars(sushi_pygments).items():
        if not name.startswith("_") or not name.lstrip("_").isupper():
            continue
        if isinstance(value, tuple) and all(isinstance(word, str) for word in value):
            vocabulary |= set(value)
    assert len(vocabulary) > 40, "the word lists were not found; the gate is vacuous"
    return vocabulary


def _grammar_words() -> set[str]:
    """Every lowercase word the grammar spells out, as a terminal or inline."""
    text = GRAMMAR.read_text(encoding="utf-8")
    words = set(re.findall(r"/([a-z_][a-z_0-9]*)\\b/", text))     # PERK.2: /perk\b/
    words |= set(re.findall(r'"([a-z_][a-z_0-9]*)"', text))       # FN: "fn"
    return words


def _significant(source: str):
    """Tokens with the whitespace dropped."""
    return [(t, v) for t, v in lexer.get_tokens(source) if v.strip()]


def _unstyled(source: str) -> list[str]:
    """Characters that reached the catch-all rule: the lexer has no rule for them."""
    return [v for t, v in _significant(source) if t is Text]


def test_every_grammar_keyword_is_known_to_the_lexer():
    """A keyword added to the grammar must be added here too. This is that gate."""
    missing = sorted(_grammar_words() - _lexer_vocabulary() - _NOT_KEYWORDS)
    assert not missing, f"the grammar has words the lexer does not know: {missing}"


@pytest.mark.parametrize("literal", [
    "42", "1_000_000",
    "0xFF", "0xDEAD_BEEF", "0b1010_1010", "0o7_7",
    "3.14", "3.141_592", "1.5e-3", "1.5E+3_0", "1_0.2_5e1_0",
    "1e10", "2_5e1_0",
])
def test_numeric_literal_is_one_token(literal):
    """Every shape the grammar's numeric terminals accept lexes whole.

    A split renders the tail of a literal as an identifier: `1e10` used to be the
    integer 1 followed by a name `e10`.
    """
    toks = _significant(literal)
    assert len(toks) == 1, f"{literal!r} split into {toks}"
    assert toks[0][0] in Number, f"{literal!r} lexed as {toks[0][0]}"


@pytest.mark.parametrize("source", [
    "let List@(i32) xs = List.new()",
    "fn id@(T)(nom T x) T:",
    "fn h@(T: Hashable)(T x) u64:",
    "fn show@(...Ts: Display)(Ts... vals) ~:",
    "let i32 a = identity@(i32)(5)",
    "struct Pair@(T, U):",
])
def test_generic_syntax_is_styled(source):
    """`@(...)` is the generic form since 0.11.0. The `@` had no rule at all."""
    assert not _unstyled(source), f"unstyled in {source!r}: {_unstyled(source)}"


@pytest.mark.parametrize("source", [
    'println("{arr[0]}")',
    'println("{x.get(0)??}")',
    'println("{a == b}")',
    'println("{n != 0}")',
    'println("Hello, {name}!")',
])
def test_interpolated_expression_is_styled(source):
    """An interpolation holds an ordinary expression, and reads like one."""
    assert not _unstyled(source), f"unstyled in {source!r}: {_unstyled(source)}"


def _corpus_files() -> list[Path]:
    roots = ["docs", "tests", "sushi_lang/sushi_stdlib/src_sushi", "toolchain/src"]
    files = []
    for root in roots:
        directory = PROJECT_ROOT / root
        if directory.exists():
            # A `test_err_` fixture may hold characters the language does not have;
            # that is what it asserts. Every other file is valid Sushi.
            files += [f for f in directory.rglob("*.sushi")
                      if not f.name.startswith("test_err_")]
    return sorted(files)


def test_the_corpus_has_no_character_the_lexer_cannot_place():
    """The empirical gate. It is what caught `@`, 1679 times over."""
    corpus = _corpus_files()
    assert len(corpus) > 1000, f"the corpus sweep found only {len(corpus)} files"

    offenders: dict[str, str] = {}
    for path in corpus:
        for char in _unstyled(path.read_text(encoding="utf-8")):
            offenders.setdefault(char, str(path.relative_to(PROJECT_ROOT)))
    assert not offenders, f"characters with no lexer rule: {offenders}"
