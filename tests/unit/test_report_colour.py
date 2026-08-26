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
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_slib_doc_carriage import DOC_LIB, build_library  # noqa: E402

from sushi_lang.internals.styling import should_colour  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TOOL_SRC = REPO / "toolchain" / "src" / "slib_info.sushi"

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _env(**overrides):
    env = dict(os.environ)
    for name in ("NO_COLOR", "CLICOLOR_FORCE", "TERM", "SUSHI_TOOLCHAIN_BIN"):
        env.pop(name, None)
    env["SUSHI_TOOLCHAIN"] = "off"
    env.update(overrides)
    return env


class _Stream(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


# ------------------------------------------------------------------ the ladder

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("NO_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(name, raising=False)


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

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    tmp = tmp_path_factory.mktemp("slibcolour")
    slib, _metadata = build_library(tmp, "doclib", DOC_LIB)
    tool = tmp / "slib-info"
    r = _run(["sushic", str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return slib, tool


def test_a_piped_report_carries_no_escape(built):
    slib, tool = built
    assert "\x1b" not in _run([str(tool), "--docs", str(slib)], env=_env()).stdout
    py = _run(["sushic", "--lib-info", str(slib), "--docs"], env=_env())
    assert "\x1b" not in py.stdout


@pytest.mark.parametrize("how", [["--color=always"], []], ids=["flag", "variable"])
def test_forcing_colour_paints_the_report(built, how):
    slib, tool = built
    env = _env() if how else _env(CLICOLOR_FORCE="1")
    assert "\x1b[" in _run([str(tool), *how, "--docs", str(slib)], env=env).stdout
    py = _run(["sushic", "--lib-info", str(slib), "--docs", *how], env=env)
    assert "\x1b[" in py.stdout


def test_never_beats_a_forcing_variable(built):
    slib, tool = built
    env = _env(CLICOLOR_FORCE="1")
    assert "\x1b" not in _run([str(tool), "--color=never", str(slib)], env=env).stdout
    py = _run(["sushic", "--lib-info", str(slib), "--color=never"], env=env)
    assert "\x1b" not in py.stdout


def test_colour_changes_no_text_at_all(built):
    """R43's real constraint: strip the escapes and the plain report comes back.

    Stated on the PLAIN report, which is all signatures and has no prose to render.
    The documented report has one deliberate exception -- R40 replaces an inline
    Markdown mark with a style -- and `test_slib_info_markdown.py` states it there.
    """
    slib, tool = built
    plain = _run([str(tool), str(slib)], env=_env()).stdout
    painted = _run([str(tool), "--color=always", str(slib)], env=_env()).stdout
    assert painted != plain
    assert ANSI.sub("", painted) == plain


@pytest.mark.parametrize("extra", [[], ["--docs"]])
def test_the_two_implementations_agree_in_colour(built, extra):
    slib, tool = built
    tool_run = _run([str(tool), "--color=always", *extra, str(slib)], env=_env())
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    py_run = _run(["sushic", "--lib-info", str(slib), "--color=always", *extra],
                  env=_env())
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    assert py_run.stdout.endswith(tool_run.stdout)


def test_a_section_header_and_a_symbol_name_are_bold(built):
    slib, tool = built
    out = _run([str(tool), "--color=always", str(slib)], env=_env()).stdout
    assert "\x1b[1mPublic Functions\x1b[0m" in out
    assert "\x1b[1mplain_add\x1b[0m" in out


def test_a_tag_keyword_is_blue_and_a_parameter_name_is_cyan(built):
    slib, tool = built
    out = _run([str(tool), "--color=always", "--docs", str(slib)], env=_env()).stdout
    assert "\x1b[34mReturns\x1b[0m: " in out
    assert "\x1b[34mParameter\x1b[0m \x1b[36ma\x1b[0m: " in out


# ------------------------------------------------------------------- the banner

def test_the_banner_obeys_no_color(built):
    """It used to decide on `isatty` alone, so NO_COLOR silenced everything but it."""
    slib, _tool = built
    forced = _run(["sushic", "--lib-info", str(slib)], env=_env(CLICOLOR_FORCE="1"))
    assert "\x1b[" in forced.stdout.splitlines()[0]

    quiet = _run(["sushic", "--lib-info", str(slib)],
                 env=_env(CLICOLOR_FORCE="1", NO_COLOR="1"))
    assert "\x1b" not in quiet.stdout


@pytest.fixture()
def stub_bin(tmp_path):
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    stub = bin_dir / "slib-info"
    stub.write_text('#!/bin/sh\necho "ARGS: $*"\n')
    stub.chmod(0o755)
    return bin_dir


def test_the_delegation_forwards_the_switch(built, stub_bin):
    slib, _tool = built
    env = _env()
    env.pop("SUSHI_TOOLCHAIN")
    env["SUSHI_TOOLCHAIN_BIN"] = str(stub_bin)
    assert "--color=never" in _run(
        ["sushic", "--lib-info", str(slib), "--color=never"], env=env).stdout
    # `auto` is the default and says nothing, so it is not worth a word on the line.
    assert "--color" not in _run(["sushic", "--lib-info", str(slib)], env=env).stdout
