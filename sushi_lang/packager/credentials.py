"""Credential storage for Omakase API tokens."""
from __future__ import annotations

import os
import tomllib

from sushi_lang.packager.constants import SUSHI_HOME

CREDENTIALS_FILE = SUSHI_HOME / "credentials.toml"


def toml_escape(s: str) -> str:
    """Escape a string for a hand-written TOML basic string / quoted key.

    The stdlib has a TOML reader (tomllib) but no writer, so the few writers in
    the packager build lines by hand. Unescaped, a quote, backslash or newline
    in a value corrupts the whole file -- and the corrupted read used to be
    silently swallowed downstream.
    """
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)


def load_token(repository: str) -> str | None:
    """Load the API token for a repository. Returns None if not found."""
    if not CREDENTIALS_FILE.exists():
        return None
    with open(CREDENTIALS_FILE, "rb") as f:
        data = tomllib.load(f)
    entry = data.get(repository, {})
    return entry.get("token")


def save_token(repository: str, token: str) -> None:
    """Save an API token for a repository. Creates or updates credentials.toml."""
    data: dict[str, dict] = {}
    if CREDENTIALS_FILE.exists():
        with open(CREDENTIALS_FILE, "rb") as f:
            data = tomllib.load(f)

    data[repository] = {"token": token}
    _write_credentials(data)


def remove_token(repository: str) -> bool:
    """Remove the token for a repository. Returns True if a token was removed."""
    if not CREDENTIALS_FILE.exists():
        return False
    with open(CREDENTIALS_FILE, "rb") as f:
        data = tomllib.load(f)
    if repository not in data:
        return False
    del data[repository]
    _write_credentials(data)
    return True


def _write_credentials(data: dict[str, dict]) -> None:
    """Write credentials dict to TOML file with 0600 permissions."""
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for repo, values in data.items():
        lines.append(f'["{toml_escape(repo)}"]')
        for key, val in values.items():
            lines.append(f'{key} = "{toml_escape(str(val))}"')
        lines.append("")
    content = "\n".join(lines) + "\n" if lines else ""

    # Write with restrictive permissions
    fd = os.open(str(CREDENTIALS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode())
    finally:
        os.close(fd)
