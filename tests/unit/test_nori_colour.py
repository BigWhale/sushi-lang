"""nori decides colour through the one styling seam (#986).

These tests call the packager's Python code; none of them compiles Sushi.
"""
from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

import pytest

from sushi_lang.internals import styling
from sushi_lang.packager import cli

PACKAGER_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang" / "packager"
ESC = "\x1b["

# A decision about INPUT (prompt with getpass, or read a pipe), not about colour.
ISATTY_ALLOWED = {"commands/login.py": "sys.stdin.isatty()"}


@pytest.fixture(autouse=True)
def _clean_colour_env(monkeypatch):
    for name in ("NO_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(name, raising=False)
    styling.set_colour_override(None)
    yield
    styling.set_colour_override(None)


def _set_ladder(monkeypatch, rung: str) -> None:
    if rung == "no_color":
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.setenv("CLICOLOR_FORCE", "1")


def _run_version(argv: list[str]) -> None:
    cli.run(cli.build_parser().parse_args(argv))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _search(monkeypatch) -> None:
    from sushi_lang.packager.commands import search

    payload = {
        "packages": [{"name": "towel", "latest_version": "1.0", "license": "MIT",
                      "total_downloads": 42, "description": "Mostly Harmless"}],
        "pagination": {"total": 1, "page": 1, "per_page": 20},
    }
    monkeypatch.setattr(search.urllib.request, "urlopen",
                        lambda *a, **k: _Response(json.dumps(payload).encode()))
    args = argparse.Namespace(repository="omakase.example.net", query="towel",
                              namespace="stable", platform=None, sort="relevance",
                              page=1, per_page=20)
    assert search.cmd_search(args) == 0


def _status(monkeypatch) -> None:
    from sushi_lang.packager.commands import status

    monkeypatch.setattr(status, "load_token", lambda repo: "nori_token")
    monkeypatch.setattr(status, "api_request", lambda *a, **k: {
        "username": "arthur", "email": "arthur@example.net", "packages": []})
    args = argparse.Namespace(repository="omakase.example.net")
    assert status.cmd_status(args) == 0


RENDERERS = {
    "banner": lambda mp: _run_version(["--version"]),
    "search": _search,
    "status": _status,
}


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("renderer", sorted(RENDERERS))
def test_no_color_prints_no_escape_on_a_terminal(renderer, monkeypatch):
    terminal = _Terminal()
    monkeypatch.setattr("sys.stdout", terminal)
    _set_ladder(monkeypatch, "no_color")
    RENDERERS[renderer](monkeypatch)
    assert terminal.getvalue() and ESC not in terminal.getvalue()


@pytest.mark.parametrize("renderer", sorted(RENDERERS))
def test_clicolor_force_paints_a_pipe(renderer, monkeypatch, capsys):
    _set_ladder(monkeypatch, "force")
    RENDERERS[renderer](monkeypatch)
    assert ESC in capsys.readouterr().out


def test_color_flag_beats_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    _run_version(["--color", "never", "--version"])
    assert ESC not in capsys.readouterr().out

    monkeypatch.setenv("NO_COLOR", "1")
    _run_version(["--color", "always", "--version"])
    assert ESC in capsys.readouterr().out


def test_nori_banner_names_nori(monkeypatch, capsys):
    _run_version(["--version"])
    out = capsys.readouterr().out
    assert "Nori" in out and "Sushi" not in out


def test_the_packager_asks_no_stream_about_colour():
    """`should_colour` is the one colour decision; a bare isatty() is a second one."""
    hits = []
    for path in sorted(PACKAGER_ROOT.rglob("*.py")):
        rel = path.relative_to(PACKAGER_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "isatty(" not in line:
                continue
            allowed = ISATTY_ALLOWED.get(rel)
            if allowed is not None and allowed in line:
                continue
            hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == []


def test_the_packager_writes_no_raw_escape():
    pattern = re.compile(r"\\x1b|\\033|\\u001b")
    hits = [
        f"{path.relative_to(PACKAGER_ROOT)}:{n}"
        for path in sorted(PACKAGER_ROOT.rglob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []
