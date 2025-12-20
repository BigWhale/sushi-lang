from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Any

from lark import Token

class C:
    """ANSI color/style escape codes."""
    RESET = "\x1b[0m"
    BOLD  = "\x1b[1m"
    DIM   = "\x1b[2m"
    RED   = "\x1b[31m"
    YELLOW = "\x1b[33m"
    CYAN  = "\x1b[36m"
    GRAY  = "\x1b[90m"

@dataclass
class Span:
    line: int
    col: int
    end_line: int
    end_col: int

@dataclass
class Diagnostic:
    kind: str
    code: str
    message: str
    span: Optional[Span] = None
    filename: Optional[str] = None  # Unit-specific filename for multi-file reporting

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


class Reporter:
    def __init__(self, source: Optional[str] = None, filename: str = "<input>") -> None:
        self.source = source
        self.filename = filename
        self.items: List[Diagnostic] = []

    def error(self, code: str, msg: str, span: Optional[Span]):
        self.items.append(Diagnostic("error", code, msg, span, filename=self.filename))

    def warn(self, code: str, msg: str, span: Optional[Span]):
        self.items.append(Diagnostic("warning", code, msg, span, filename=self.filename))

    @property
    def has_errors(self) -> bool:
        return any(d.kind == "error" for d in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(d.kind == "warning" for d in self.items)

    def format(self, use_color: bool = True, use_unicode: bool = True) -> str:
        """Render all diagnostics.

        use_color   → ANSI colorize location/kind/guide/markers
        use_unicode → use │ / ╰ / ╯ and a separate caret line above the guide
        """
        out: List[str] = []
        src_lines = self.source.splitlines() if self.source else None

        for d in self.items:
            # ----- header (same as before, abbreviated) -----
            # Use diagnostic-specific filename if available (for multi-file reporting), otherwise fall back to reporter filename
            filename = d.filename or self.filename

            # Convert absolute path to relative path with ./ prefix
            try:
                from pathlib import Path
                abs_path = Path(filename).resolve()
                cwd = Path.cwd()
                rel_path = abs_path.relative_to(cwd)
                filename = f"./{rel_path}"
            except (ValueError, Exception):
                # If relative_to fails (different drives, etc), just use basename
                from pathlib import Path
                filename = Path(filename).name

            loc = f"{filename}:{d.span.line}:{d.span.col}" if d.span else filename

            # Ensure message ends with period
            message = d.message if d.message.endswith('.') else f"{d.message}."

            if use_color:
                kind = f"{C.BOLD}{C.RED}error{C.RESET}" if d.kind == "error" else f"{C.BOLD}{C.YELLOW}warning{C.RESET}"
                head = f"{C.CYAN}{loc}{C.RESET}: {kind} [{C.DIM}{d.code}{C.RESET}]: {message}"
            else:
                head = f"{loc}: {d.kind} [{d.code}]: {message}"

            # ----- snippet block -----
            if d.span:
                # Determine which source to use for this diagnostic
                diagnostic_src_lines = src_lines

                # If diagnostic has a different filename, read its source
                if d.filename and d.filename != self.filename:
                    try:
                        from pathlib import Path
                        diagnostic_source = Path(d.filename).read_text(encoding="utf-8")
                        diagnostic_src_lines = diagnostic_source.splitlines()
                    except Exception:
                        diagnostic_src_lines = None  # Fallback if we can't read the file

                if diagnostic_src_lines is not None:
                    line_idx = d.span.line - 1
                    line_text = diagnostic_src_lines[line_idx] if 0 <= line_idx < len(diagnostic_src_lines) else ""
                else:
                    line_text = ""

                # 1-based columns; ensure at least one column
                start = max(1, d.span.col)
                end = max(start, d.span.end_col)  # kept for future multi-char spans

                if use_unicode:
                    # New format with top curve
                    if use_color:
                        gray = lambda s: f"{C.GRAY}{s}{C.RESET}"
                        error_color = C.RED if d.kind == "error" else C.YELLOW

                    # Line 1: Top curve with error message
                    top_curve = "  ╭──┤ "
                    if use_color:
                        out.append(f"{gray(top_curve)}{head}")
                    else:
                        out.append(f"{top_curve}{head}")

                    # Line 2: Source code line (indented by 1 space)
                    line_prefix = "  │  "
                    if use_color:
                        out.append(f"{gray('  │')}{' ' * 1}{line_text}")
                    else:
                        out.append(f"{line_prefix}{line_text}")

                    # Line 3: Caret line (indented by 1 space to align with source)
                    caret_prefix = "  │  "
                    caret = " " * (start - 1) + "┯"
                    if use_color:
                        colored_caret = f"{error_color}{caret}{C.RESET}"
                        out.append(f"{gray('  │')}{' ' * 1}{colored_caret}")
                    else:
                        out.append(f"{caret_prefix}{caret}")

                    # Line 4: Bottom curve
                    guide_len = start  # +1 for the indent space
                    guide = "─" * guide_len + "╯"
                    guide_prefix = "  ╰"
                    if use_color:
                        colored_guide = f"{C.GRAY}{'─' * guide_len}{C.RESET}" + \
                                       f"{error_color}╯{C.RESET}"
                        out.append(f"{gray(guide_prefix)}{colored_guide}")
                    else:
                        out.append(f"{guide_prefix}{guide}")

                else:
                    # ASCII fallback: header on top, then source and caret
                    out.append(head)
                    line_prefix  = "  | "
                    caret_prefix = "  ` "
                    out.append(f"{line_prefix}{line_text}")
                    out.append(f"{caret_prefix}{' ' * (start - 1) + '^'}")
            else:
                # No span - just output the header
                out.append(head)

        return "\n".join(out)

    def print(self, stream=None, use_color: Optional[bool] = None, use_unicode: Optional[bool] = None) -> None:
        """Print diagnostics to `stream` (default: sys.stderr).

        Color is auto-enabled for TTY unless NO_COLOR or TERM=dumb.
        Unicode prefixes (│ / ╰) are auto-enabled for TTY unless NO_UNICODE or TERM=dumb.
        """
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
