from __future__ import annotations
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

    def emit(self) -> None:
        # Diagnostic was already appended in error_with/warn_with
        pass


class Reporter:
    def __init__(self, source: Optional[str] = None, filename: str = "<input>") -> None:
        self.source = source
        self.filename = filename
        self.items: List[Diagnostic] = []

    def error(self, code: str, msg: str, span: Optional[Span]):
        self.items.append(Diagnostic("error", code, msg, span, filename=self.filename))

    def warn(self, code: str, msg: str, span: Optional[Span]):
        self.items.append(Diagnostic("warning", code, msg, span, filename=self.filename))

    def error_with(self, code: str, msg: str, span: Optional[Span]) -> DiagnosticBuilder:
        d = Diagnostic("error", code, msg, span, filename=self.filename)
        self.items.append(d)
        return DiagnosticBuilder(self, d)

    def warn_with(self, code: str, msg: str, span: Optional[Span]) -> DiagnosticBuilder:
        d = Diagnostic("warning", code, msg, span, filename=self.filename)
        self.items.append(d)
        return DiagnosticBuilder(self, d)

    @property
    def has_errors(self) -> bool:
        return any(d.kind == "error" for d in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(d.kind == "warning" for d in self.items)

    def _resolve_filename(self, filename: str) -> str:
        """Convert absolute path to relative path with ./ prefix."""
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
        end = max(start, span.end_col)
        span_len = end - start + 1

        if use_unicode:
            if span_len <= 1:
                marker = " " * (start - 1) + "\u252c"
            else:
                left = span_len // 2
                right = span_len - left - 1
                marker = " " * (start - 1) + "\u2500" * left + "\u252c" + "\u2500" * right
            if use_color:
                gray = lambda s: f"{C.GRAY}{s}{C.RESET}"
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

            if d.span:
                diagnostic_src_lines = self._get_source_lines(d.filename or self.filename, src_lines)

                if diagnostic_src_lines is not None:
                    line_idx = d.span.line - 1
                    line_text = diagnostic_src_lines[line_idx] if 0 <= line_idx < len(diagnostic_src_lines) else ""
                else:
                    line_text = ""

                start = max(1, d.span.col)
                end = max(start, d.span.end_col)

                if use_unicode:
                    if use_color:
                        gray = lambda s: f"{C.GRAY}{s}{C.RESET}"
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

                    span_len = end - start + 1
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

                    # Check if any sub-diagnostics have spans (continuous box)
                    has_span_subs = any(s.span for s in d.sub)

                    guide_len = start + (span_len // 2 if span_len > 1 else 0)

                    if not has_span_subs:
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
                    span_len = end - start + 1
                    if span_len <= 1:
                        ascii_marker = " " * (start - 1) + "^"
                    else:
                        left = span_len // 2
                        right = span_len - left - 1
                        ascii_marker = " " * (start - 1) + "-" * left + "+" + "-" * right
                    out.append(f"{line_prefix}{line_text}")
                    out.append(f"{caret_prefix}{ascii_marker}")
            else:
                out.append(head)

            # Render sub-diagnostics (notes, help)
            span_subs = [s for s in d.sub if s.span]
            no_span_subs = [s for s in d.sub if not s.span]

            for i, sub in enumerate(span_subs):
                sub_filename = sub.filename or d.filename or self.filename
                sub_filename = self._resolve_filename(sub_filename)
                sub_loc = f"{sub_filename}:{sub.span.line}:{sub.span.col}"
                sub_src_lines = self._get_source_lines(sub.filename or d.filename or self.filename, src_lines)
                is_last = (i == len(span_subs) - 1)

                if use_unicode:
                    sub_kind_color = C.BLUE if sub.kind == "note" else C.BOLD
                    if use_color:
                        out.append(f"{C.GRAY}  \u2502{C.RESET}")
                        sub_label = f"{C.CYAN}{sub_loc}{C.RESET}: {sub_kind_color}{sub.kind}{C.RESET}: {sub.message}"
                        out.append(f"{C.GRAY}  \u251c\u2500\u2500\u2524{C.RESET} {sub_label}")
                    else:
                        out.append(f"  \u2502")
                        out.append(f"  \u251c\u2500\u2500\u2524 {sub_loc}: {sub.kind}: {sub.message}")
                    note_color = C.BLUE if use_color else ""
                    self._render_snippet(sub.span, sub_src_lines, note_color, use_color, use_unicode, out)

                    if is_last:
                        sub_start = max(1, sub.span.col)
                        sub_end = max(sub_start, sub.span.end_col)
                        sub_span_len = sub_end - sub_start + 1
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
                    self._render_snippet(sub.span, sub_src_lines, "", use_color, use_unicode, out, prefix="    ")

            for sub in no_span_subs:
                sub_kind_color = C.BLUE if sub.kind == "note" else C.BOLD
                if use_color:
                    out.append(f"  = {sub_kind_color}{sub.kind}{C.RESET}: {sub.message}")
                else:
                    out.append(f"  = {sub.kind}: {sub.message}")

        return "\n".join(out)

    def print(self, stream=None, use_color: Optional[bool] = None, use_unicode: Optional[bool] = None) -> None:
        """Print diagnostics to `stream` (default: sys.stderr)."""
        import os, sys
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
