"""THE colour decision, and the palette every renderer paints from.

One ladder, read by everything that writes to a terminal: the diagnostics, the version
banner and the `--lib-info` report. Three sites is what it took to make this a seam --
`report.py` implemented three of the five rungs for diagnostics, and `version.py`
implemented one of them for the banner, so `NO_COLOR` silenced a diagnostic and left the
line above it painted.
"""
from __future__ import annotations

import os
from typing import Optional


class C:
    """ANSI color/style escape codes."""
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    ITALIC = "\x1b[3m"
    RED = "\x1b[31m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    CYAN = "\x1b[36m"
    GRAY = "\x1b[90m"


# What a caller may say when it has been told explicitly. `auto` says nothing, which is
# the same as saying nothing at all.
COLOUR_CHOICES = ("auto", "always", "never")

_override: Optional[str] = None


def set_colour_override(choice: Optional[str]) -> None:
    """Install the process-wide answer a `--color` flag gave, for every later decision.

    A CLI flag IS process-wide, and the banner, a diagnostic and the report are decided
    in three different places at three different times. Passing the answer down to all
    three would thread one flag through every call in the compiler to change the colour
    of a line.
    """
    global _override
    _override = None if choice in (None, "auto") else choice


def should_colour(stream, override: Optional[str] = None) -> bool:
    """Does `stream` get ANSI escapes? Highest precedence first.

    1. `always` / `never`, from `--color` or from this call -- an explicit answer wins.
    2. `NO_COLOR` set to ANYTHING, the empty string included. That is no-color.org's
       rule: the variable's PRESENCE is the signal, never its value.
    3. `CLICOLOR_FORCE` set to anything but `0` -- on, terminal or not. This rung is what
       makes a coloured report testable: a pipe is not a terminal, so without it every
       gate would compare the plain report and call it a pass.
    4. `TERM=dumb` -- off.
    5. The stream is a terminal.
    """
    choice = override if override not in (None, "auto") else _override
    if choice is not None:
        return choice == "always"

    if os.getenv("NO_COLOR") is not None:
        return False
    force = os.getenv("CLICOLOR_FORCE")
    if force is not None and force != "0":
        return True
    if os.getenv("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Palette:
    """The report's styles, as strings: the real escapes, or nothing at all.

    A style is CONCATENATED and never branched on at the call site, so a painted line and
    a plain one are written once. `docs/design/documentation.md` R43 is the map, and its
    one constraint is that painting changes no text -- strip the escapes from a coloured
    report and the plain report comes back.
    """

    __slots__ = ("bold", "dim", "italic", "blue", "cyan", "reset")

    def __init__(self, on: bool):
        self.bold = C.BOLD if on else ""
        self.dim = C.DIM if on else ""
        self.italic = C.ITALIC if on else ""
        self.blue = C.BLUE if on else ""
        self.cyan = C.CYAN if on else ""
        self.reset = C.RESET if on else ""
