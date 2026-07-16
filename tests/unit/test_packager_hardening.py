"""Unit tests for the Tier 5.4 packager hardening.

Covers the hand-written TOML writers (stdlib has a reader but no writer), the
login command's argv surface, and the entry-point shape.
"""
from __future__ import annotations

import tomllib

import pytest

from sushi_lang.packager import credentials as creds


HOSTILE = 'quote " backslash \\ newline \n tab \t control \x01 end'


# --------------------------------------------------------------------------
# TOML escaping: tomllib must read back exactly what was written
# --------------------------------------------------------------------------

def test_credentials_round_trip_hostile_values(tmp_path, monkeypatch):
    """A token containing quotes/backslashes/newlines must survive the write ->
    read cycle byte-for-byte. Unescaped, it corrupted the whole file (and the
    corrupted read used to be silently swallowed)."""
    monkeypatch.setattr(creds, "CREDENTIALS_FILE", tmp_path / "credentials.toml")

    creds.save_token("omakase.example.net", HOSTILE)
    assert creds.load_token("omakase.example.net") == HOSTILE


def test_credentials_round_trip_hostile_repository_key(tmp_path, monkeypatch):
    """The repository name is a quoted TOML key and needs the same escaping."""
    monkeypatch.setattr(creds, "CREDENTIALS_FILE", tmp_path / "credentials.toml")
    hostile_repo = 'repo"with\\quotes'

    creds.save_token(hostile_repo, "nori_token")
    assert creds.load_token(hostile_repo) == "nori_token"


def test_credentials_file_is_0600(tmp_path, monkeypatch):
    monkeypatch.setattr(creds, "CREDENTIALS_FILE", tmp_path / "credentials.toml")
    creds.save_token("omakase.example.net", "nori_token")
    assert (tmp_path / "credentials.toml").stat().st_mode & 0o777 == 0o600


def test_toml_escape_is_tomllib_valid():
    """Every escaped string must parse back to the original via tomllib."""
    for raw in ("plain", HOSTILE, "\\", '"', "\r\n", "\x7f"):
        doc = f'v = "{creds.toml_escape(raw)}"\n'
        assert tomllib.loads(doc)["v"] == raw


# --------------------------------------------------------------------------
# login: the API key must not be an argv surface
# --------------------------------------------------------------------------

def test_login_parser_takes_no_api_key_argv():
    """An argv key leaks into shell history and ps output; the parser must
    reject one so the only paths are the getpass prompt and stdin."""
    from sushi_lang.packager.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["login", "nori_somekey"])
    args = parser.parse_args(["login"])
    assert not hasattr(args, "api_key")


def test_login_reads_key_from_piped_stdin(monkeypatch):
    import io
    from sushi_lang.packager.commands.login import _read_api_key

    monkeypatch.setattr("sys.stdin", io.StringIO("nori_piped_key\n"))
    assert _read_api_key() == "nori_piped_key"


# --------------------------------------------------------------------------
# Entry point: main() returns an int (testable), __main__ exists
# --------------------------------------------------------------------------

def test_main_returns_int(monkeypatch, capsys):
    import sushi_lang.packager as pkg

    monkeypatch.setattr("sys.argv", ["nori"])
    rc = pkg.main()
    assert isinstance(rc, int)


def test_dunder_main_module_exists():
    import importlib.util

    assert importlib.util.find_spec("sushi_lang.packager.__main__") is not None
