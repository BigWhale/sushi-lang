from __future__ import annotations
import os
import re
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Any

from lark import Token

# The palette and the colour ladder live in `styling`, which the version banner reads
# too. `C` is re-exported because this module's renderers are its oldest caller.
from sushi_lang.internals.styling import C, should_colour  # noqa: F401

# The spelling gate (#734, widened by #759). A diagnostic renders for a USER, so it may
# carry only spellings the user can write. Two are refused. One renderer, `display_type()`,
# answers the first for many emit sites, and nothing asked the emit sites until this gate.
SPELLING_GATE_ENV = "SUSHI_SPELLING_GATE"

# Source syntax is `@(...)`; `<...>` is the INTERNAL identity name and a table key.
# Every legitimate `<` in a diagnostic follows a quote, a space or a line start: the
# operators `'<'` and `'<<'`, the placeholders `<value>` and `<expression>`, the import
# path `<collections/strings>`. An identifier character in front of one is an interned
# name. Measured over the fixture corpus: 4 hits in 1338 diagnostic lines, all four real.
_INTERNED_SPELLING = re.compile(r"[A-Za-z0-9_]<")

# A borrow is written `peek x` / `poke x`. The `&` form was retired when borrow-by-default
# landed, and the parser refuses it, so a diagnostic that prints one hands the user a
# repair the compiler then rejects (#744). Only the two mode words are read, so a bitwise
# `&` is untouched. Measured over the fixture corpus: 0 hits in 2020 diagnostic lines, and
# one spelling put back answers 2 -- a zero here is a measured zero, not a dead pattern.
_RETIRED_BORROW = re.compile(r"&\s*(?:peek|poke)\b")

# Each row is a pattern and the repair its message names.
_REFUSED_SPELLINGS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_INTERNED_SPELLING,
     "names a type with the internal spelling. Render it with display_type()"),
    (_RETIRED_BORROW,
     "spells a borrow with the retired `&`. Write the bare `peek` / `poke`"),
)


class DiagnosticSpellingError(BaseException):
    """A diagnostic carries a spelling the user cannot write.

    It derives from `BaseException` on purpose. The compiler catches `Exception` at two
    levels and renders a CE0000 from it, which would hide the emit site behind a generic
    crash and let a fixture that asserts the exit code alone report a pass. This one has
    to reach the developer with the traceback that names the line, because the line is
    the whole answer.
    """

    def __init__(self, code: str, text: str, repair: str) -> None:
        super().__init__(
            f"{SPELLING_GATE_ENV}: diagnostic {code} {repair}. Message: {text!r}")
        self.code = code
        self.text = text
        self.repair = repair


def spelling_gate_is_on() -> bool:
    """Is the gate armed? OFF unless the environment says otherwise.

    A cosmetic fault is never worth a user-facing crash, so the default stays off and
    the test runner arms it.
    """
    return os.environ.get(SPELLING_GATE_ENV, "").lower() not in ("", "0", "off")


def check_spelling(code: str, text: str) -> None:
    """Refuse a spelling the user cannot write, in one piece of diagnostic text."""
    if not text or not spelling_gate_is_on():
        return
    for pattern, repair in _REFUSED_SPELLINGS:
        if pattern.search(text):
            raise DiagnosticSpellingError(code, text, repair)


@dataclass
class Span:
    line: int
    col: int
    end_line: int
    end_col: int

@dataclass
class SubDiagnostic:
    kind: str  # "note", "help"
    message: str
    span: Optional[Span] = None
    filename: Optional[str] = None

@dataclass
class Diagnostic:
    kind: str
    code: str
    message: str
    span: Optional[Span] = None
    filename: Optional[str] = None
    sub: List[SubDiagnostic] = field(default_factory=list)
    # Whether to draw the source line and the caret. False for a diagnostic whose
    # span covers a WHOLE construct: the caret then marks everything and separates
    # nothing, while the header already carries the location. It is also the only
    # honest rendering of a multi-line span, because the marker takes its width from
    # the span's last line and is drawn under its first.
    show_source: bool = True
    # The text this span indexes into, when it is not a file on disk: a library
    # template arrives as a source slice in the manifest, and its spans belong to the
    # slice and to nothing else (#471). It rides on the diagnostic so it survives the
    # per-unit reporters being merged into the top-level one.
    source: Optional[str] = None

@dataclass(frozen=True)
class Origin:
    """The file a span belongs to, when it is not the file the reporter covers.

    One reporter serves many files in two places. A binary `.slib` ships a public generic
    as a source SLICE, re-parsed at the consumer, so the instance's spans are line 1 of
    that slice and mean nothing against the consumer's file (#471). And the collect pass
    walks every unit through one reporter, so a declaration in a non-entry unit rendered
    against the entry file names a line the user did not write (#473).

    Each field answers one question: what to call it, what text the caret marks, and why
    it is being compiled here at all. `source` is None when the file is on disk and the
    renderer can read it, and `provenance` is None when there is nothing to explain --
    a unit of the program the user is compiling needs no note.
    """

    filename: str
    source: Optional[str] = None
    provenance: Optional[str] = None


def diagnostic_identity(diagnostic: Diagnostic) -> tuple:
    """What makes two diagnostics the same REPORT: everything the user reads first.

    One source read twice says one thing about one line, so the second copy is noise --
    a body shared by many monomorphized instances (#648), or an instantiation of a
    generic-target extension. Notes are excluded deliberately: two instances can attach
    different secondary locations to the same finding, and the user still wants to be
    told once. The MESSAGE is included, so a finding that genuinely differs by type
    argument is a different report and survives.
    """
    span = diagnostic.span
    return (
        diagnostic.kind,
        diagnostic.code,
        diagnostic.message,
        diagnostic.filename,
        None if span is None else (span.line, span.col, span.end_line, span.end_col),
    )


def in_source_order(items: List[Diagnostic]) -> List[Diagnostic]:
    """`items` ordered by where they are in the source, one file at a time.

    The per-unit passes each walk the whole unit, so what they emit is in pass order and
    a reader wants the file. A file keeps the place its FIRST diagnostic gave it -- the
    order the passes reached the files in is information, and alphabetical is not -- and
    inside a file the order is the line, then the column. A diagnostic with no span is
    about the file as a whole and heads its group. The sort is stable, so two findings on
    one caret keep the order the passes made them in.
    """
    groups: dict[Optional[str], int] = {}
    for d in items:
        groups.setdefault(d.filename, len(groups))
    return sorted(items, key=lambda d: (
        groups[d.filename],
        (d.span.line, d.span.col) if d.span is not None else (-1, -1),
    ))


def span_of(t: Any) -> Optional[Span]:
    m = getattr(t, "meta", None)
    if m is not None:
        return Span(m.line, m.column, m.end_line, m.end_column)
    if isinstance(t, Token):
        line = getattr(t, "line", None)
        col = getattr(t, "column", None)
        end_line = getattr(t, "end_line", None)
        end_col = getattr(t, "end_column", None)
        if line is not None and col is not None:
            return Span(line, col, end_line or line, end_col or col)
    return None


def display_filename(filename: str) -> str:
    """The name a diagnostic prints for `filename`: `./` plus the path relative to the
    working directory, or the base name of a file outside it.

    An `assert` prints the same name at run time, so the two always agree.
    """
    # A name in angle brackets is not a path and has no directory to strip:
    # `<input>` for a single string, `<template:lib:name>` for a slice a library
    # shipped. Prefixing `./` onto one made it read as a file next door.
    if filename.startswith("<") and filename.endswith(">"):
        return filename
    try:
        rel_path = Path(filename).resolve().relative_to(Path.cwd())
        return f"./{rel_path}"
    except (ValueError, OSError):
        return Path(filename).name


class DiagnosticBuilder:
    """Builder for attaching sub-diagnostics (notes, help) before emitting.

    A note has two entry points, and the name is the contract (#731):

    - `note_at(message, span)` is a RELATIONAL note. It points at a second place, and the
      span is not optional. A caller that holds an `Optional[Span]` must decide what the
      `None` means before it calls: a span gives a note, and no span gives no note, or a
      `help` when the message still says something without a location.
    - `note(message)` is PROSE. It carries no location, by design.

    There is no blanket guard that refuses a note with no location, and none must be
    added. Many notes are prose and correct as prose: the four suspended guarantees of an
    `unsafe external` block, "'x.len()' is a method, not a field", a library record that
    carries no span. A guard would refuse those. The fault to prevent is a relational note
    that loses its location without a decision, and the split signature prevents it: mypy
    refuses an `Optional[Span]` at `note_at`, and `note` has no place for a span.
    """

    def __init__(self, diagnostic: Diagnostic):
        self._diagnostic = diagnostic

    def note(self, message: str) -> DiagnosticBuilder:
        check_spelling(self._diagnostic.code, message)
        self._diagnostic.sub.append(SubDiagnostic("note", message))
        return self

    def note_at(self, message: str, span: Span, filename: Optional[str] = None) -> DiagnosticBuilder:
        check_spelling(self._diagnostic.code, message)
        self._diagnostic.sub.append(SubDiagnostic("note", message, span, filename))
        return self

    def help(self, message: str) -> DiagnosticBuilder:
        check_spelling(self._diagnostic.code, message)
        self._diagnostic.sub.append(SubDiagnostic("help", message))
        return self

    def location_only(self) -> DiagnosticBuilder:
        """Drop the source line and the caret; the header keeps the location."""
        self._diagnostic.show_source = False
        return self

    def emit(self) -> None:
        # Diagnostic was already appended in error_with/warn_with
        pass


class Reporter:
    def __init__(self, source: Optional[str] = None, filename: Optional[str] = "<input>",
                 provenance: Optional[str] = None,
                 keeps_warnings: Optional[bool] = None) -> None:
        self.source = source
        self.filename = filename
        # A one-line explanation of whose code this reporter covers, attached as a note
        # to every diagnostic it raises. Set for a unit that came from a source library,
        # where the failure is in code the consumer never wrote and an unattributed
        # error would be unreadable.
        self.provenance = provenance
        # A warning belongs to the author of the code (#1007). A reporter over code the
        # consumer did not write records its errors and drops its warnings, so they
        # neither print nor change the exit status. The caller can say otherwise: the
        # stdlib gates make the bundled stdlib's author the one who compiles it.
        self.keeps_warnings = provenance is None if keeps_warnings is None else keeps_warnings
        # Set while a pass checks a body that is NOT this reporter's file: a
        # monomorphized instance of a binary library's template. Its spans came from
        # parsing the manifest slice, so rendering them against the consumer's file
        # names a line the consumer never wrote (#471). One place stamps it, because
        # `_record` is the one place every diagnostic passes through.
        self.origin: Optional[Origin] = None
        # Set while a pass reads the body of a MONOMORPHIZED INSTANCE. Every instance of
        # one generic carries the TEMPLATE's spans, so a fault in the shared source would
        # be told once per instantiation (#648). While it is set, a diagnostic whose
        # identity has already been recorded is dropped. It is not a general
        # de-duplicator: a repeat anywhere else is a bug to be fixed where it is made,
        # and stays visible.
        self.collapse_repeats: bool = False
        self.items: List[Diagnostic] = []
        self._identities: set = set()
        # Every error offered, by code, a dropped repeat included, so "did this walk
        # report that fault" has one answer in every copy of an instance body.
        self.errors_offered: Counter[str] = Counter()

    def enter_body(self, func) -> None:
        """Tell the reporter whose body a pass is about to read.

        Two answers ride on the same moment, and every per-unit pass needs both.
        `origin` is whose FILE the spans belong to -- a transplanted library template's
        came from the manifest slice, not from the consumer's file (#471).
        `collapse_repeats` is whether this body is one of many copies of one source.
        """
        self.origin = getattr(func, "library_origin", None)
        self.collapse_repeats = getattr(func, "instance_of", None) is not None

    def leave_body(self) -> None:
        """Clear what `enter_body` set, for a walk that reads no function body."""
        self.origin = None
        self.collapse_repeats = False

    def _record(self, d: Diagnostic) -> Diagnostic:
        # The one funnel every diagnostic passes: `error`, `warn` and the two builder
        # openers all arrive here, so the gate sits here and asks once (#734). A note or
        # a help added later is asked by the builder, because it arrives after this.
        check_spelling(d.code, d.message)
        for sub in d.sub:
            check_spelling(d.code, sub.message)
        if self.origin is not None:
            # An emit site that named a file of its own keeps it; `error()` fills the
            # reporter's own name in otherwise, and that is the one to replace.
            if d.filename == self.filename:
                d.filename = self.origin.filename
                d.source = self.origin.source
            if self.origin.provenance is not None:
                d.sub.append(SubDiagnostic("note", self.origin.provenance))
        elif self.provenance:
            d.sub.append(SubDiagnostic("note", self.provenance))
        if d.kind == "warning" and not self._author_compiles():
            return d
        if d.kind == "error":
            self.errors_offered[d.code] += 1
        # AFTER the origin fixups: they can change the file a span is read against, and
        # the file is part of what makes two reports the same one.
        identity = diagnostic_identity(d)
        if self.collapse_repeats and identity in self._identities:
            return d
        self._identities.add(identity)
        self.items.append(d)
        return d

    def _author_compiles(self) -> bool:
        """Is the code under report the compiling user's own? A library template's
        instance never is; otherwise the reporter's unit decides."""
        if self.origin is not None and self.origin.provenance is not None:
            return False
        return self.keeps_warnings

    def error(self, code: str, msg: str, span: Optional[Span], filename: Optional[str] = None):
        self._record(Diagnostic("error", code, msg, span, filename=filename or self.filename))

    def warn(self, code: str, msg: str, span: Optional[Span], filename: Optional[str] = None):
        self._record(Diagnostic("warning", code, msg, span, filename=filename or self.filename))

    def error_with(self, code: str, msg: str, span: Optional[Span],
                   filename: Optional[str] = None) -> DiagnosticBuilder:
        d = self._record(Diagnostic("error", code, msg, span, filename=filename or self.filename))
        return DiagnosticBuilder(d)

    def warn_with(self, code: str, msg: str, span: Optional[Span],
                  filename: Optional[str] = None) -> DiagnosticBuilder:
        d = self._record(Diagnostic("warning", code, msg, span, filename=filename or self.filename))
        return DiagnosticBuilder(d)

    @property
    def has_errors(self) -> bool:
        return any(d.kind == "error" for d in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(d.kind == "warning" for d in self.items)

    def _get_source_lines(self, filename: str, src_lines: Optional[List[str]]) -> Optional[List[str]]:
        """Get source lines for a file, reading from disk if needed."""
        if filename == self.filename:
            return src_lines
        try:
            return Path(filename).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None

    def _render_snippet(self, span: Span, source_lines: Optional[List[str]],
                        color: str, use_color: bool, use_unicode: bool,
                        out: List[str], prefix: str = "  ") -> int:
        """Render a source line and its marker; answer the column the close guide ends at."""
        line_text = ""
        if source_lines is not None and 0 <= span.line - 1 < len(source_lines):
            line_text = source_lines[span.line - 1]

        start = max(1, span.col)
        if span.end_line > span.line:
            # The span runs past this line and only this line is drawn, so it is
            # underlined to its end. The width taken from the span's LAST line measured
            # it against text the caret is not drawn under.
            end = max(start, len(line_text) + 1)
        else:
            end = max(start, span.end_col)

        marker = _marker(start, end, use_unicode)
        if use_unicode:
            bar = _paint(C.GRAY, prefix + "│", use_color)
            out.append(f"{bar} {line_text}")
            out.append(f"{bar} {_paint(color, marker, use_color)}")
        else:
            out.append(f"{prefix}| {line_text}")
            out.append(f"{prefix}` {marker}")
        return _guide(start, end)

    def format(self, use_color: bool = True, use_unicode: bool = True) -> str:
        """Render all diagnostics."""
        out: List[str] = []
        src_lines = self.source.splitlines() if self.source else None
        for d in self.items:
            self._render_diagnostic(d, src_lines, use_color, use_unicode, out)
        return "\n".join(out)

    def _render_diagnostic(self, d: Diagnostic, src_lines: Optional[List[str]],
                           use_color: bool, use_unicode: bool, out: List[str]) -> None:
        name = d.filename or self.filename
        loc = ""
        if name:
            filename = display_filename(name)
            loc = f"{filename}:{d.span.line}:{d.span.col}" if d.span else filename
        message = d.message if d.message.endswith('.') else f"{d.message}."
        kind_style = C.BOLD + (C.RED if d.kind == "error" else C.YELLOW)
        where = f"{_paint(C.CYAN, loc, use_color)}: " if loc else ""
        head = (f"{where}{_paint(kind_style, d.kind, use_color)} "
                f"[{_paint(C.DIM, d.code, use_color)}]: {message}")

        if d.span and d.show_source:
            lines = (d.source.splitlines() if d.source is not None
                     else self._get_source_lines(d.filename or self.filename or "", src_lines))
            if use_unicode:
                tip = C.RED if d.kind == "error" else C.YELLOW
                out.append(f"{_paint(C.GRAY, '  ╭──┤ ', use_color)}{head}")
                guide = self._render_snippet(d.span, lines, tip, use_color, use_unicode, out)
                # The box stays open for ANY sub-diagnostic: a note carries its own
                # snippet, and a help is rendered inside the box too.
                corner = "├" if d.sub else "╰"
                out.append(_paint(C.GRAY, f"  {corner}", use_color)
                           + _paint(C.GRAY, "─" * guide, use_color)
                           + _paint(tip, "╯", use_color))
            else:
                out.append(head)
                self._render_snippet(d.span, lines, "", use_color, use_unicode, out)
        elif d.span and use_unicode:
            # Location only: the header names the line and column, and there is no
            # caret because there is nothing on the line to separate.
            out.append(f"{_paint(C.GRAY, '  ╭──┤', use_color)} {head}")
            if not d.sub:
                out.append(_box_end(use_color))
        else:
            out.append(head)

        # A help never carries a location, so it can only be rendered after every note.
        # Inside the box when there is a box; the trailing `= help:` form otherwise.
        in_box = bool(use_unicode and d.span)
        prose = [s for s in d.sub if not s.span]
        self._render_span_subs(d, src_lines, use_color, use_unicode, in_box and bool(prose), out)
        self._render_prose_subs(prose, use_color, in_box, out)

    def _render_span_subs(self, d: Diagnostic, src_lines: Optional[List[str]],
                          use_color: bool, use_unicode: bool, box_continues: bool,
                          out: List[str]) -> None:
        located = [(s, s.span) for s in d.sub if s.span is not None]
        for i, (sub, sub_span) in enumerate(located):
            sub_filename = display_filename(sub.filename or d.filename or self.filename or "")
            sub_loc = f"{sub_filename}:{sub_span.line}:{sub_span.col}"
            sub_lines = (
                d.source.splitlines() if d.source is not None and sub.filename is None
                else self._get_source_lines(sub.filename or d.filename or self.filename or "", src_lines)
            )
            kind = _paint(_sub_style(sub.kind), sub.kind, use_color)

            if not use_unicode:
                out.append(f"  = {kind}: {sub.message}")
                out.append(f"    {_paint(C.CYAN, sub_loc, use_color)}")
                self._render_snippet(sub_span, sub_lines, "", use_color, use_unicode, out, prefix="    ")
                continue

            out.append(_box_bar(use_color))
            label = f"{_paint(C.CYAN, sub_loc, use_color)}: {kind}: {sub.message}"
            out.append(f"{_paint(C.GRAY, '  ├──┤', use_color)} {label}")
            guide = self._render_snippet(sub_span, sub_lines, C.BLUE, use_color, use_unicode, out)
            if i == len(located) - 1 and not box_continues:
                out.append(_paint(C.GRAY, f"  ╰{'─' * guide}╯", use_color))

    @staticmethod
    def _render_prose_subs(prose: List[SubDiagnostic], use_color: bool, in_box: bool,
                           out: List[str]) -> None:
        for index, sub in enumerate(prose):
            style = _sub_style(sub.kind)
            if not in_box:
                out.append(f"  = {_paint(style, sub.kind, use_color)}: {sub.message}")
                continue
            # The label stays: a span-less NOTE is a fact and a HELP is advice, and
            # inside the box nothing else tells the two apart.
            if index == 0:
                out.append(_box_bar(use_color))
            label = f"{sub.kind}: "
            lines = textwrap.wrap(sub.message, width=76 - len(label)) or [""]
            for offset, line in enumerate(lines):
                lead = _paint(style, label, use_color) if offset == 0 else " " * len(label)
                out.append(f"{_box_bar(use_color)}  {lead}{line}")
        if in_box and prose:
            out.append(_box_end(use_color))

    def print(self, stream=None, use_color: Optional[bool] = None, use_unicode: Optional[bool] = None) -> None:
        """Print diagnostics to `stream` (default: sys.stderr)."""
        stream = stream or sys.stderr

        if use_color is None:
            use_color = should_colour(stream)

        if use_unicode is None:
            is_tty = getattr(stream, "isatty", lambda: False)()
            no_unicode = os.getenv("NO_UNICODE") is not None
            dumb = os.getenv("TERM") == "dumb"
            use_unicode = bool(is_tty and not no_unicode and not dumb)

        text = self.format(use_color=use_color, use_unicode=use_unicode)
        if text:
            print(text, file=stream)


def _paint(style: str, text: str, use_color: bool) -> str:
    """`text` in `style` when colour is on, and bare otherwise."""
    return f"{style}{text}{C.RESET}" if use_color else text


def _box_bar(use_color: bool) -> str:
    return _paint(C.GRAY, "  │", use_color)


def _box_end(use_color: bool) -> str:
    return _paint(C.GRAY, "  ╰───", use_color)


def _sub_style(kind: str) -> str:
    return C.BLUE if kind == "note" else C.BOLD


def _marker(start: int, end: int, use_unicode: bool) -> str:
    """The underline from column `start` to the EXCLUSIVE `end`, its tick in the middle."""
    dash, tick, single = ("─", "┬", "┬") if use_unicode else ("-", "+", "^")
    width = max(1, end - start)
    if width <= 1:
        return " " * (start - 1) + single
    left = width // 2
    return " " * (start - 1) + dash * left + tick + dash * (width - left - 1)


def _guide(start: int, end: int) -> int:
    """The column a box's close guide runs to: under the marker's tick."""
    width = max(1, end - start)
    return start + (width // 2 if width > 1 else 0)
