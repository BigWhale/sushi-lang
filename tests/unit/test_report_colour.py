"""R41 and R43: one colour decision, one style map, and text that colour cannot change.

The ladder, highest precedence first:

1. `--color=always` / `--color=never` -- an explicit answer wins;
2. `NO_COLOR` set to ANYTHING, the empty string included (no-color.org's rule) -- off;
3. `CLICOLOR_FORCE` set to anything but `0` -- on, terminal or not;
4. `TERM=dumb` -- off;
5. the stream is a terminal -- on, else off.

Rung 3 is what makes a coloured report testable at all: a pipe is not a terminal, so
without a forced-colour switch every gate that exists would compare the plain report and
call it a pass.

`sushic --lib-info` and the Sushi tool share the ladder, and so do the diagnostics and the
banner -- the banner used to decide on `isatty` alone, so `NO_COLOR` silenced a diagnostic
and not the line above it.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


from sushi_lang.internals.styling import should_colour  # noqa: E402








class _Stream(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


# ------------------------------------------------------------------ the ladder



@pytest.mark.parametrize("tty,expected", [(True, True), (False, False)])
def test_a_terminal_decides_when_nothing_else_does(tty, expected):
    assert should_colour(_Stream(tty)) is expected


@pytest.mark.parametrize("value", ["1", "0", "", "anything"])
def test_no_color_is_off_whatever_it_says(monkeypatch, value):
    """no-color.org: the variable's PRESENCE is the signal, not its value."""
    monkeypatch.setenv("NO_COLOR", value)
    assert should_colour(_Stream(True)) is False


def test_clicolor_force_beats_a_pipe(monkeypatch):
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    assert should_colour(_Stream(False)) is True


def test_clicolor_force_of_zero_is_not_a_force(monkeypatch):
    monkeypatch.setenv("CLICOLOR_FORCE", "0")
    assert should_colour(_Stream(False)) is False


def test_no_color_beats_clicolor_force(monkeypatch):
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    assert should_colour(_Stream(True)) is False


def test_a_dumb_terminal_is_off(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert should_colour(_Stream(True)) is False


def test_a_forced_dumb_terminal_is_on(monkeypatch):
    """Rung 3 stands above rung 4: a caller that forces colour has answered already."""
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    assert should_colour(_Stream(False)) is True


@pytest.mark.parametrize("override,expected", [("always", True), ("never", False)])
def test_an_explicit_answer_wins_over_every_variable(monkeypatch, override, expected):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("TERM", "dumb")
    assert should_colour(_Stream(False), override) is expected


def test_auto_falls_through_to_the_variables(monkeypatch):
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    assert should_colour(_Stream(False), "auto") is True


# ------------------------------------------------------------------ the report

















# ------------------------------------------------------------------- the banner





