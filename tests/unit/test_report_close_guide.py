"""The close guide of a Unicode diagnostic box ends under the tick of its marker (#998).

The column is calculated one time, in `_render_snippet`, from the marker that it draws.
A span that runs past its first line is underlined to the end of that line, so a guide
calculated again from the raw `end_col` (a column on the LAST line) misses the tick.

The renderer is built in Python from a source string; nothing here parses Sushi.
"""
from __future__ import annotations

import re

import pytest

from sushi_lang.internals.report import Reporter, Span

_MATCH = (
    "enum A:\n"
    "    X\n"
    "enum B:\n"
    "    Y\n"
    "\n"
    "fn mk(i32 a, i32 b) A:\n"
    "    return Result.Ok(A.X)\n"
    "\n"
    "fn main() i32:\n"
    "    match mk(1,\n"
    "            2).realise(A.X):\n"
    "        B.Y -> println(\"y\")\n"
    "    return Result.Ok(0)\n"
)

_SHORT = (
    "fn main() i32:\n"
    "    let i32 value = 1\n"
    "    let i32 other = 2\n"
    "    return Result.Ok(0)\n"
)


def _guide_past_the_tick() -> Reporter:
    """A guide from the raw end column runs PAST the tick."""
    r = Reporter(_MATCH, filename="<input>")
    r.error_with("CE2107", "arm names enum 'B', the value is 'A'", Span(12, 9, 12, 12)) \
        .note_at("the value matched here is 'A'", Span(10, 11, 11, 27))
    return r


def _guide_short_of_the_tick() -> Reporter:
    """A guide from the raw end column stops SHORT of the tick."""
    r = Reporter(_SHORT, filename="<input>")
    r.warn_with("CW2001", "multi-line note", Span(2, 5, 2, 8)) \
        .note_at("multi", Span(2, 5, 4, 3))
    return r


def _one_line_note() -> Reporter:
    r = Reporter(_SHORT, filename="<input>")
    r.error_with("CE2002", "one-line note", Span(3, 21, 3, 22)) \
        .note_at("declared here", Span(2, 9, 2, 12))
    return r


CASES = [
    pytest.param(_guide_past_the_tick, id="past-the-tick"),
    pytest.param(_guide_short_of_the_tick, id="short-of-the-tick"),
    pytest.param(_one_line_note, id="one-line"),
]


def _guides_and_ticks(text: str) -> list[tuple[int, int]]:
    """Each close guide's `╯` column, paired with the tick column of the marker above it."""
    pairs = []
    tick = None
    for line in text.splitlines():
        if "┬" in line:
            tick = line.index("┬")
        if "╯" in line:
            assert tick is not None, text
            pairs.append((line.index("╯"), tick))
            tick = None
    return pairs


@pytest.mark.parametrize("make", CASES)
@pytest.mark.parametrize("use_color", [False, True], ids=["plain", "painted"])
def test_every_close_guide_ends_under_its_tick(make, use_color):
    text = re.sub(r"\x1b\[[0-9;]*m", "", make().format(use_color=use_color, use_unicode=True))
    pairs = _guides_and_ticks(text)
    assert len(pairs) == 2, text
    for guide, tick in pairs:
        assert guide == tick, text
