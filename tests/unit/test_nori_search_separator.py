"""nori search prints the same text with colour on and off (#999).

The test calls the packager's Python code with a stand-in server response; it compiles no
Sushi and uses no network.
"""
from __future__ import annotations

import argparse
import io
import json
import re

import pytest

from sushi_lang.internals import styling
from sushi_lang.packager.commands import search

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SEPARATOR = "  " + "  ".join("─" * w for w in (5, 7, 12, 9, 11))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _search_output(monkeypatch, capsys, variable: str) -> str:
    for name in ("NO_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "1")
    payload = {
        "packages": [{"name": "towel", "latest_version": "1.0.0", "license": "MIT",
                      "total_downloads": 42, "description": "Mostly Harmless"}],
        "pagination": {"total": 1, "page": 1, "per_page": 20},
    }
    monkeypatch.setattr(search.urllib.request, "urlopen",
                        lambda *a, **k: _Response(json.dumps(payload).encode()))
    args = argparse.Namespace(repository="omakase.example.net", query="towel",
                              namespace=None, platform=None, sort=None,
                              page=1, per_page=20)
    assert search.cmd_search(args) == 0
    return capsys.readouterr().out


@pytest.fixture(autouse=True)
def _no_override():
    styling.set_colour_override(None)
    yield
    styling.set_colour_override(None)


def test_colour_changes_no_text_in_the_search_table(monkeypatch, capsys):
    plain = _search_output(monkeypatch, capsys, "NO_COLOR")
    painted = _search_output(monkeypatch, capsys, "CLICOLOR_FORCE")

    assert "\x1b[" not in plain
    assert "\x1b[" in painted
    assert ANSI.sub("", painted) == plain
    assert SEPARATOR in plain.splitlines()
