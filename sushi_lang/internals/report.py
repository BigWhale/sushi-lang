from __future__ import annotations
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional, Any

from lark import Token

class C:
    """ANSI color/style escape codes."""
    RESET = "\x1b[0m"
    BOLD  = "\x1b[1m"
    DIM   = "\x1b[2m"
    RED   = "\x1b[31m"
    YELLOW = "\x1b[33m"
    BLUE  = "\x1b[34m"
    CYAN  = "\x1b[36m"
    GRAY  = "\x1b[90m"

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


class DiagnosticBuilder:
    """Builder for attaching sub-diagnostics (notes, help) before emitting."""

    def __init__(self, reporter: Reporter, diagnostic: Diagnostic):
        self._reporter = reporter
        self._diagnostic = diagnostic

    def note(self, message: str, span: Optional[Span] = None, filename: Optional[str] = None) -> DiagnosticBuilder:
        self._diagnostic.sub.append(SubDiagnostic("note", message, span, filename))
        return self

    def help(self, message: str) -> DiagnosticBuilder:
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
    def __init__(self, source: Optional[str] = None, filename: str = "<input>",
                 provenance: Optional[str] = None) -> None:
        self.source = source
        self.filename = filename
        # A one-line explanation of whose code this reporter covers, attached as a note
        # to every diagnostic it raises. Set for a unit that came from a source library,
        # where the failure is in code the consumer never wrote and an unattributed
        # error would be unreadable.
        self.provenance = provenance
        # Set while a pass checks a body that is NOT this reporter's file: a
        # monomorphized instance of a binary library's template. Its spans came from
        # parsing the manifest slice, so rendering them against the consumer's file
        # names a line the consumer never wrote (#471). One place stamps it, because
        # `_record` is the one place every diagnostic passes through.
        self.origin: Optional[Origin] = None
        self.items: List[Diagnostic] = []

    def _record(self, d: Diagnostic) -> Diagnostic:
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
        self.items.append(d)
        return d

    def error(self, code: str, msg: str, span: Optional[Span], filename: Optional[str] = None):
        self._record(Diagnostic("error", code, msg, span, filename=filename or self.filename))

    def warn(self, code: str, msg: str, span: Optional[Span], filename: Optional[str] = None):
        self._record(Diagnostic("warning", code, msg, span, filename=filename or self.filename))

    def error_with(self, code: str, msg: str, span: Optional[Span],
                   filename: Optional[str] = None) -> DiagnosticBuilder:
        d = self._record(Diagnostic("error", code, msg, span, filename=filename or self.filename))
        return DiagnosticBuilder(self, d)

    def warn_with(self, code: str, msg: str, span: Optional[Span],
                  filename: Optional[str] = None) -> DiagnosticBuilder:
        d = self._record(Diagnostic("warning", code, msg, span, filename=filename or self.filename))
        return DiagnosticBuilder(self, d)

    @property
    def has_errors(self) -> bool:
        return any(d.kind == "error" for d in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(d.kind == "warning" for d in self.items)

    def _resolve_filename(self, filename: str) -> str:
        """Convert absolute path to relative path with ./ prefix."""
        # A name in angle brackets is not a path and has no directory to strip:
        # `<input>` for a single string, `<template:lib:name>` for a slice a library
        # shipped. Prefixing `./` onto one made it read as a file next door.
        if filename.startswith("<") and filename.endswith(">"):
            return filename
        try:
            from pathlib import Path
            abs_path = Path(filename).resolve()
            cwd = Path.cwd()
            rel_path = abs_path.relative_to(cwd)
            return f"./{rel_path}"
        except (ValueError, Exception):
            from pathlib import Path
            return Path(filename).name

    def _get_source_lines(self, filename: str, src_lines: Optional[List[str]]) -> Optional[List[str]]:
        """Get source lines for a file, reading from disk if needed."""
        if filename == self.filename:
            return src_lines
        try:
            from pathlib import Path
            return Path(filename).read_text(encoding="utf-8").splitlines()
        except Exception:
            return None

    def _render_snippet(self, span: Span, source_lines: Optional[List[str]],
                        color: str, use_color: bool, use_unicode: bool,
                        out: List[str], prefix: str = "  ") -> None:
        """Render a source code snippet with underline marker."""
        if source_lines is not None:
            line_idx = span.line - 1
            line_text = source_lines[line_idx] if 0 <= line_idx < len(source_lines) else ""
        else:
            line_text = ""

        start = max(1, span.col)
        if span.end_line > span.line:
            end = max(start, len(line_text) + 1)   # only this line is drawn
        else:
            end = max(start, span.end_col)
        span_len = max(1, end - start)             # `end_col` is exclusive

        if use_unicode:
            if span_len <= 1:
                marker = " " * (start - 1) + "\u252c"
            else:
                left = span_len // 2
                right = span_len - left - 1
                marker = " " * (start - 1) + "\u2500" * left + "\u252c" + "\u2500" * right
            if use_color:
                def gray(s: str) -> str:
                    return f"{C.GRAY}{s}{C.RESET}"
                out.append(f"{gray(prefix + chr(0x2502))}{' ' * 1}{line_text}")
                out.append(f"{gray(prefix + chr(0x2502))}{' ' * 1}{color}{marker}{C.RESET}")
            else:
                out.append(f"{prefix}\u2502  {line_text}")
                out.append(f"{prefix}\u2502  {marker}")
        else:
            if span_len <= 1:
                ascii_marker = " " * (start - 1) + "^"
            else:
                left = span_len // 2
                right = span_len - left - 1
                ascii_marker = " " * (start - 1) + "-" * left + "+" + "-" * right
            out.append(f"{prefix}| {line_text}")
            out.append(f"{prefix}` {ascii_marker}")

    def format(self, use_color: bool = True, use_unicode: bool = True) -> str:
        """Render all diagnostics."""
        out: List[str] = []
        src_lines = self.source.splitlines() if self.source else None

        for d in self.items:
            filename = d.filename or self.filename
            filename = self._resolve_filename(filename)

            loc = f"{filename}:{d.span.line}:{d.span.col}" if d.span else filename

            message = d.message if d.message.endswith('.') else f"{d.message}."

            if use_color:
                kind = f"{C.BOLD}{C.RED}error{C.RESET}" if d.kind == "error" else f"{C.BOLD}{C.YELLOW}warning{C.RESET}"
                head = f"{C.CYAN}{loc}{C.RESET}: {kind} [{C.DIM}{d.code}{C.RESET}]: {message}"
            else:
                head = f"{loc}: {d.kind} [{d.code}]: {message}"

            if d.span and d.show_source:
                diagnostic_src_lines = (
                    d.source.splitlines() if d.source is not None
                    else self._get_source_lines(d.filename or self.filename, src_lines)
                )

                if diagnostic_src_lines is not None:
                    line_idx = d.span.line - 1
                    line_text = diagnostic_src_lines[line_idx] if 0 <= line_idx < len(diagnostic_src_lines) else ""
                else:
                    line_text = ""

                start = max(1, d.span.col)
                if d.span.end_line > d.span.line:
                    # The span runs past this line and only this line is drawn, so it
                    # is underlined to its end. Taking the width from the span's LAST
                    # line measures it against text the caret is not drawn under: a
                    # `const_def` ends at column 1 of the line after the declaration,
                    # which rendered as a one-character caret under the first keyword.
                    end = max(start, len(line_text) + 1)
                else:
                    end = max(start, d.span.end_col)

                if use_unicode:
                    if use_color:
                        def gray(s: str) -> str:
                            return f"{C.GRAY}{s}{C.RESET}"
                        error_color = C.RED if d.kind == "error" else C.YELLOW

                    top_curve = "  ╭──┤ "
                    if use_color:
                        out.append(f"{gray(top_curve)}{head}")
                    else:
                        out.append(f"{top_curve}{head}")

                    line_prefix = "  │  "
                    if use_color:
                        out.append(f"{gray('  │')}{' ' * 1}{line_text}")
                    else:
                        out.append(f"{line_prefix}{line_text}")

                    # `end_col` is EXCLUSIVE, as Lark reports it and as every span
                    # the compiler builds by hand spells it (`col + len(text)`), so
                    # the width is the difference. Adding one drew a marker one
                    # character wider than its own token, every time.
                    span_len = max(1, end - start)
                    if span_len <= 1:
                        marker = " " * (start - 1) + "\u252c"
                    else:
                        left = span_len // 2
                        right = span_len - left - 1
                        marker = " " * (start - 1) + "\u2500" * left + "\u252c" + "\u2500" * right

                    if use_color:
                        out.append(f"{gray('  \u2502')}{' ' * 1}{error_color}{marker}{C.RESET}")
                    else:
                        out.append(f"{line_prefix}{marker}")

                    # The box stays open for ANY sub-diagnostic: a note carries its
                    # own snippet, and a help is now rendered inside the box too.
                    has_subs = bool(d.sub)

                    guide_len = start + (span_len // 2 if span_len > 1 else 0)

                    if not has_subs:
                        if use_color:
                            out.append(f"{gray('  \u2570')}{C.GRAY}{'\u2500' * guide_len}{C.RESET}{error_color}\u256f{C.RESET}")
                        else:
                            out.append(f"  \u2570{'\u2500' * guide_len}\u256f")
                    else:
                        if use_color:
                            out.append(f"{gray('  \u251c')}{C.GRAY}{'\u2500' * guide_len}{C.RESET}{error_color}\u256f{C.RESET}")
                        else:
                            out.append(f"  \u251c{'\u2500' * guide_len}\u256f")

                else:
                    out.append(head)
                    line_prefix  = "  | "
                    caret_prefix = "  ` "
                    span_len = max(1, end - start)
                    if span_len <= 1:
                        ascii_marker = " " * (start - 1) + "^"
                    else:
                        left = span_len // 2
                        right = span_len - left - 1
                        ascii_marker = " " * (start - 1) + "-" * left + "+" + "-" * right
                    out.append(f"{line_prefix}{line_text}")
                    out.append(f"{caret_prefix}{ascii_marker}")
            elif d.span and use_unicode:
                # Location only: the header names the line and column, and there is
                # no caret because there is nothing on the line to separate.
                if use_color:
                    out.append(f"{C.GRAY}  \u256d\u2500\u2500\u2524{C.RESET} {head}")
                else:
                    out.append(f"  \u256d\u2500\u2500\u2524 {head}")
                if not d.sub:
                    out.append(f"{C.GRAY}  \u2570\u2500\u2500\u2500{C.RESET}"
                               if use_color else "  \u2570\u2500\u2500\u2500")
            else:
                out.append(head)

            # Render sub-diagnostics (notes, help)
            span_subs = [s for s in d.sub if s.span]
            no_span_subs = [s for s in d.sub if not s.span]
            # A help never carries a location, so it can only be rendered after every
            # note. Inside the box when there is a box; the old trailing `= help:`
            # form otherwise.
            in_box = bool(use_unicode and d.span)

            for i, sub in enumerate(span_subs):
                sub_span = sub.span
                assert sub_span is not None  # span_subs is filtered on s.span above
                sub_filename = sub.filename or d.filename or self.filename
                sub_filename = self._resolve_filename(sub_filename)
                sub_loc = f"{sub_filename}:{sub_span.line}:{sub_span.col}"
                sub_src_lines = (
                    d.source.splitlines() if d.source is not None and sub.filename is None
                    else self._get_source_lines(
                        sub.filename or d.filename or self.filename, src_lines)
                )
                is_last = (i == len(span_subs) - 1) and not (in_box and no_span_subs)

                if use_unicode:
                    sub_kind_color = C.BLUE if sub.kind == "note" else C.BOLD
                    if use_color:
                        out.append(f"{C.GRAY}  \u2502{C.RESET}")
                        sub_label = f"{C.CYAN}{sub_loc}{C.RESET}: {sub_kind_color}{sub.kind}{C.RESET}: {sub.message}"
                        out.append(f"{C.GRAY}  \u251c\u2500\u2500\u2524{C.RESET} {sub_label}")
                    else:
                        out.append("  \u2502")
                        out.append(f"  \u251c\u2500\u2500\u2524 {sub_loc}: {sub.kind}: {sub.message}")
                    note_color = C.BLUE if use_color else ""
                    self._render_snippet(sub_span, sub_src_lines, note_color, use_color, use_unicode, out)

                    if is_last:
                        sub_start = max(1, sub_span.col)
                        sub_end = max(sub_start, sub_span.end_col)
                        sub_span_len = max(1, sub_end - sub_start)
                        sub_guide = sub_start + (sub_span_len // 2 if sub_span_len > 1 else 0)
                        if use_color:
                            out.append(f"{C.GRAY}  \u2570{'\u2500' * sub_guide}\u256f{C.RESET}")
                        else:
                            out.append(f"  \u2570{'\u2500' * sub_guide}\u256f")
                else:
                    sub_kind_color = C.BLUE if (use_color and sub.kind == "note") else (C.BOLD if use_color else "")
                    if use_color:
                        out.append(f"  = {sub_kind_color}{sub.kind}{C.RESET}: {sub.message}")
                        out.append(f"    {C.CYAN}{sub_loc}{C.RESET}")
                    else:
                        out.append(f"  = {sub.kind}: {sub.message}")
                        out.append(f"    {sub_loc}")
                    self._render_snippet(sub_span, sub_src_lines, "", use_color, use_unicode, out, prefix="    ")

            for index, sub in enumerate(no_span_subs):
                sub_kind_color = C.BLUE if sub.kind == "note" else C.BOLD
                if not in_box:
                    if use_color:
                        out.append(f"  = {sub_kind_color}{sub.kind}{C.RESET}: {sub.message}")
                    else:
                        out.append(f"  = {sub.kind}: {sub.message}")
                    continue

                # The label stays: a span-less NOTE is a fact and a HELP is advice,
                # and inside the box nothing else tells the two apart.
                if index == 0:
                    out.append(f"{C.GRAY}  \u2502{C.RESET}" if use_color else "  \u2502")
                label = f"{sub.kind}: "
                lines = textwrap.wrap(sub.message, width=76 - len(label)) or [""]
                for offset, line in enumerate(lines):
                    shown = f"{sub_kind_color}{label}{C.RESET}" if use_color else label
                    lead = shown if offset == 0 else " " * len(label)
                    bar = f"{C.GRAY}  \u2502{C.RESET}" if use_color else "  \u2502"
                    out.append(f"{bar}  {lead}{line}")

            if in_box and no_span_subs:
                out.append(f"{C.GRAY}  \u2570\u2500\u2500\u2500{C.RESET}"
                           if use_color else "  \u2570\u2500\u2500\u2500")

        return "\n".join(out)

    def print(self, stream=None, use_color: Optional[bool] = None, use_unicode: Optional[bool] = None) -> None:
        """Print diagnostics to `stream` (default: sys.stderr)."""
        import os
        import sys
        stream = stream or sys.stderr

        if use_color is None:
            is_tty = getattr(stream, "isatty", lambda: False)()
            no_color = os.getenv("NO_COLOR") is not None
            dumb = os.getenv("TERM") == "dumb"
            use_color = bool(is_tty and not no_color and not dumb)

        if use_unicode is None:
            is_tty = getattr(stream, "isatty", lambda: False)()
            no_unicode = os.getenv("NO_UNICODE") is not None
            dumb = os.getenv("TERM") == "dumb"
            use_unicode = bool(is_tty and not no_unicode and not dumb)

        text = self.format(use_color=use_color, use_unicode=use_unicode)
        if text:
            print(text, file=stream)
