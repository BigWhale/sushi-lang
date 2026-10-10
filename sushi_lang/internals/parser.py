"""Lark parser setup and AST construction."""
from __future__ import annotations

import contextvars
import hashlib
import os
import pickle
import stat
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from typing import Any, Callable, Optional

import lark
from lark import Lark, Token, UnexpectedInput
from lark.exceptions import LarkError

from sushi_lang.internals.diagnostics import SushiError, SyntaxDiagnostic
from sushi_lang.internals.parse_errors import lark_to_diagnostic
from sushi_lang.internals.indenter import LangIndenter
from sushi_lang.internals.report import Reporter, Span, span_of
from sushi_lang.semantics.ast_builder import ASTBuilder

GRAMMAR_PATH = Path(__file__).parent.parent / "grammar.lark"

# The only postlexer is the indentation handler. Generics use `@(...)`, which
# closes on a real `)`, so there is no `>>` ambiguity and no generic-type
# postlexer to chain in front of it.

DOC_OPEN = "##:"


# True while the parser reads the text of an interpolation hole. That text comes from
# inside a string literal, so a `##:` or a `:##` in it is not a doc-block delimiter.
_IN_HOLE: contextvars.ContextVar[bool] = contextvars.ContextVar("in_hole", default=False)


def _outside_holes(callback: Callable[[Token], Token]) -> Callable[[Token], Token]:
    def guarded(token: Token) -> Token:
        return token if _IN_HOLE.get() else callback(token)
    return guarded


def _delimiter_span(token: Token) -> Optional[Span]:
    """The span of a bare `##:` or `:##`, which is three columns wide."""
    return span_of(token)


def _reject_unclosed_doc_block(token: Token) -> Token:
    """CE6011: a `##:` that reaches the lexer opened a block nothing closes."""
    raise SyntaxDiagnostic("CE6011", span=_delimiter_span(token))


def _reject_stray_doc_close(token: Token) -> Token:
    """CE6012: a `:##` that reaches the lexer closes a block nothing opened."""
    raise SyntaxDiagnostic("CE6012", span=_delimiter_span(token))


def _reject_nested_doc_open(token: Token) -> Token:
    """CE6013: a line-initial `##:` interior to a block, which cannot be a token.

    The block that is broken is the OUTER one -- it swallowed everything between
    the two openers -- so the caret goes on the inner opener and a note goes on
    the outer. Rendering this with a single location would be a regression.
    """
    start_line = getattr(token, "line", 1) or 1
    start_col = getattr(token, "column", 1) or 1

    for offset, line in enumerate(str(token.value).split("\n")):
        if offset == 0 or not line.lstrip().startswith(DOC_OPEN):
            continue
        col = len(line) - len(line.lstrip()) + 1
        inner = Span(start_line + offset, col, start_line + offset, col + len(DOC_OPEN))
        outer = Span(start_line, start_col, start_line, start_col + len(DOC_OPEN))
        raise SyntaxDiagnostic("CE6013", span=inner).note_at(
            "the enclosing documentation block opens here", outer)

    return token


# The options that hold Python objects. Lark does not store them in a cache file, and
# they are given again on each load.
_RUNTIME_OPTIONS = frozenset({"postlex", "lexer_callbacks"})

GRAMMAR_CACHE_ENV = "SUSHI_GRAMMAR_CACHE_DIR"


def grammar_cache_dir() -> Optional[Path]:
    """The directory for the grammar-table cache, or None for no cache.

    `SUSHI_GRAMMAR_CACHE_DIR` overrides it, and `off` turns the cache off. Otherwise it
    is the user cache directory: `~/Library/Caches/sushi` on macOS, else
    `$XDG_CACHE_HOME/sushi` or `~/.cache/sushi`.
    """
    override = os.environ.get(GRAMMAR_CACHE_ENV)
    if override:
        return None if override == "off" else Path(override)
    try:
        home = Path.home()
    except (RuntimeError, KeyError, OSError):
        return None
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "sushi"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg and os.path.isabs(xdg) else home / ".cache"
    return base / "sushi"


def _build_lark(grammar_text: str, grammar_path: Path, options: dict[str, Any]) -> Lark:
    return Lark(grammar_text, source_path=str(grammar_path), **options)


def _owned_by_me(st: os.stat_result) -> bool:
    """The entry is mine, and no other user can write it."""
    return st.st_uid == os.geteuid() and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _trusted_dir(cache_dir: Path) -> bool:
    """Make the directory if it is missing; True only if it is mine alone."""
    try:
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.lstat(cache_dir)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and _owned_by_me(st)


def _load_cached(cache_file: Path, runtime: dict[str, Any]) -> Optional[Lark]:
    """The parser in `cache_file`, or None if the file is missing, foreign or broken.

    The file is a pickle, so it is read only when it is a regular file that the user
    owns and that no other user can write.
    """
    try:
        with open(cache_file, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or not _owned_by_me(st):
                return None
            data = pickle.load(f)
        return Lark.__new__(Lark)._load(data, **runtime)
    except Exception:
        return None


def _store(parser: Lark, cache_dir: Path, cache_file: Path) -> None:
    """Write the tables to a temporary file, then rename it over `cache_file`.

    The rename is atomic, so a parallel compile reads the old file or the new one and
    never a part of one. A failure leaves the cache as it was.
    """
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix=".grammar-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            parser.save(f, exclude_options=_RUNTIME_OPTIONS)
        os.replace(tmp_name, cache_file)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def cached_lark(grammar_path: Path, cache_dir: Optional[Path], **options: Any) -> Lark:
    """A LALR `Lark` for `grammar_path`, with its tables kept in `cache_dir`.

    The file name is a hash of the grammar text, the options, and the Lark and Python
    versions, so a changed grammar never reads an old file. A file that is missing,
    broken or foreign is rebuilt, and a directory that cannot be used gives a plain
    build. No case prints a message, and no case changes the parser that comes back.
    """
    grammar_text = grammar_path.read_text(encoding="utf-8")
    runtime = {k: v for k, v in options.items() if k in _RUNTIME_OPTIONS}
    stored = {k: v for k, v in options.items() if k not in _RUNTIME_OPTIONS}
    if cache_dir is None or not hasattr(os, "geteuid") or not _trusted_dir(cache_dir):
        return _build_lark(grammar_text, grammar_path, options)

    key = hashlib.sha256("\0".join((
        grammar_text,
        repr(sorted(stored.items())),
        lark.__version__,
        "%d.%d" % sys.version_info[:2],
    )).encode("utf-8")).hexdigest()[:32]
    cache_file = cache_dir / f"{grammar_path.stem}-{key}.lark"

    loaded = _load_cached(cache_file, runtime)
    if loaded is not None:
        return loaded
    parser = _build_lark(grammar_text, grammar_path, options)
    _store(parser, cache_dir, cache_file)
    return parser


@lru_cache(maxsize=1)
def build_parser() -> Lark:
    """The Lark parser for `grammar.lark`. One instance parses any number of sources.

    It has two start symbols: `start` for a unit, and `expr` for the text of an
    interpolation hole (`parse_hole`). The tables come from `cached_lark`.

    The three doc-block diagnostics come from `lexer_callbacks`, which fire for an
    `%ignore`d terminal exactly as they do for a kept one. That is one mechanism for
    every delimiter mistake, in every position, with the caret on the delimiter --
    see docs/design/documentation.md section 4.
    """
    options: dict[str, Any] = dict(
        parser="lalr",
        start=["start", "expr"],
        propagate_positions=True,
        maybe_placeholders=False,
        postlex=LangIndenter(),
        lexer="basic",
        lexer_callbacks={
            "DOC_OPEN": _outside_holes(_reject_unclosed_doc_block),
            "DOC_CLOSE": _outside_holes(_reject_stray_doc_close),
            "DOC_BLOCK": _outside_holes(_reject_nested_doc_open),
        },
    )
    # A broken grammar.lark raises GrammarError here -- an ICE.
    return cached_lark(GRAMMAR_PATH, grammar_cache_dir(), **options)


def parse_hole(text: str):
    """Parse the text of an interpolation hole as one expression."""
    reset = _IN_HOLE.set(True)
    try:
        return build_parser().parse(text, start="expr")
    finally:
        _IN_HOLE.reset(reset)


def _string_opens_a_hole_before(line_text: str, col: int) -> bool:
    """Whether a STRING that opens an interpolation hole stands before this column.

    That is the #502 shape: a double-quoted literal inside a hole closed the
    OUTER literal, so the lexer handed the parser garbage and the error names a
    token nobody wrote. The lexer's own STRING pattern finds the tokens; a hole
    is open when the token's content holds more unescaped `{` than `}`.
    """
    import re

    for match in re.finditer(r'"(?:[^"\\]|\\.)*"', line_text[:max(col - 1, 0)]):
        content = match.group(0)[1:-1]
        opens = closes = 0
        i = 0
        while i < len(content):
            ch = content[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "{":
                opens += 1
            elif ch == "}":
                closes += 1
            i += 1
        if opens > closes:
            return True
    return False


_CONDITION_HINTS = {
    "IF": "use 'if (condition):' instead of 'if condition:'",
    "ELIF": "use 'elif (condition):' instead of 'elif condition:'",
    "WHILE": "use 'while (condition):' instead of 'while condition:'",
}

_PERK_METHOD_HINT = ("a perk method declares no type parameters; a generic method is a "
                     "plain extension method ('extend T name@(U)(...)')")


def _sole_lpar_hint(e: UnexpectedInput) -> Optional[str]:
    """The help for a position where `(` is the one expected token, read from the
    tokens before the failure. None when the position has no help."""
    parser = getattr(e, "interactive_parser", None)
    stack = getattr(getattr(parser, "parser_state", None), "value_stack", None)
    if not stack:
        return None
    kinds = [getattr(value, "type", None) for value in stack[-2:]]
    if kinds[-1] in _CONDITION_HINTS:
        return _CONDITION_HINTS[kinds[-1]]
    # A function definition takes `@(` after its name; a perk method takes only `(`.
    token = getattr(getattr(e, "token", None), "type", None)
    if kinds == ["FN", "NAME"] and token == "AT" and _inside_a_perk(stack):
        return _PERK_METHOD_HINT
    return None


def _inside_a_perk(stack: list) -> bool:
    """Whether the nearest open block keyword on the parser stack is `perk`."""
    for value in reversed(stack):
        kind = getattr(value, "type", None)
        if kind in ("PERK", "EXTERNAL"):
            return kind == "PERK"
    return False


# The lexer reads `||` as the logical operator, so `1||2` in a pattern is one OR token
# where an alternative `|` is legal.
_DOUBLE_BAR_HINT = "put a space: '1 | 2'"

# `public` before the two `extend` forms that carry no marker
# (docs/design/extension-visibility.md R8, R9).
_UNMARKED_EXTEND_HINTS = {
    "WITH": "a perk implementation has no `public` marker: it is as visible as its "
            "target type. Remove `public`",
    "AS": "a conversion has no `public` marker: it goes with its target type. "
          "Remove `public`",
}


def _public_extend_hint(e: UnexpectedInput) -> Optional[str]:
    """The help for `public extend T with P:` and `public extend A as B:`, else None."""
    token_type = getattr(getattr(e, "token", None), "type", None)
    hint = _UNMARKED_EXTEND_HINTS.get(token_type) if isinstance(token_type, str) else None
    if hint is None:
        return None
    parser = getattr(e, "interactive_parser", None)
    stack = getattr(getattr(parser, "parser_state", None), "value_stack", None) or []
    kinds = [getattr(value, "type", None) for value in stack]
    if "EXTEND" not in kinds:
        return None
    at = len(kinds) - 1 - kinds[::-1].index("EXTEND")
    return hint if at > 0 and kinds[at - 1] == "PUBLIC" else None


def parse_error_hint(e: UnexpectedInput, src: str = "") -> Optional[str]:
    """Advice for a parse failure the grammar cannot phrase itself. None if none applies."""
    expected = getattr(e, "expected", None)
    if expected is not None and set(expected) == {"LPAR"}:
        hint = _sole_lpar_hint(e)
        if hint is not None:
            return hint

    token = getattr(e, "token", None)
    if (expected is not None and "BIT_OR" in expected
            and getattr(token, "type", None) == "OR" and str(token) == "||"):
        return _DOUBLE_BAR_HINT

    hint = _public_extend_hint(e)
    if hint is not None:
        return hint

    line = getattr(e, "line", None)
    col = getattr(e, "column", None)
    if src and line is not None and col is not None:
        lines = src.splitlines()
        if 1 <= line <= len(lines) and _string_opens_a_hole_before(lines[line - 1], col):
            return ("a double-quoted string cannot stand inside an interpolation "
                    "hole; use single quotes inside the hole, or bind the "
                    "expression to a local first")

    return None


def parse_to_ast(src: str, dump_parse: bool = False,
                 reporter: Optional[Reporter] = None,
                 source_label: Optional[str] = None):
    """Parse source code into an AST.

    `reporter` is the file's own. With one the AST builder batches its diagnostics
    into it instead of stopping at the first (#641), and the caller stops on
    `has_errors`. A caller that parses a source of its own -- a library template, a
    bundled stdlib module -- passes none and reads one exception. `source_label` names
    such a source as its diagnostics do; an `assert` prints it at run time.
    """
    try:
        tree = build_parser().parse(src, start="start")
    except SushiError:
        raise
    except LarkError as e:
        hint = parse_error_hint(e, src) if isinstance(e, UnexpectedInput) else None
        raise lark_to_diagnostic(e, hint) from e

    if dump_parse:
        print(tree.pretty())

    ast_builder = ASTBuilder(reporter, source_label)
    return ast_builder.build(tree), tree
