"""MkDocs hook: show the message of a diagnostic code when the reader hovers its link.

Every reference to a code in the manual links to its entry in `error-catalog.md`. This
hook reads the message of each entry from that page and writes it into the `title` of
every link that points at the entry. Material's `content.tooltips` feature renders the
title as a popup, and a click still follows the link to the catalog.

The catalog page is the one source: the hook does not import the compiler, because the
docs build runs in a throw-away mkdocs environment that does not have sushi_lang.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

CATALOG = "error-catalog.md"

_ENTRY = re.compile(r"^### \S+ \{#((?:ce|cw|re|ne)[0-9]{4})\}\s*$")
_MESSAGE = re.compile(r"^\*\*Message:\*\* (`+) ?(.*?) ?\1\s*$")
_LINK = re.compile(r"<a ([^>]*)>")
_HREF = re.compile(
    r'\bhref="(?:[^"#]*error-catalog(?:/|\.html)?)?#((?:ce|cw|re|ne)[0-9]{4})"'
)

_messages: dict[str, str] = {}


def read_messages(catalog: str) -> dict[str, str]:
    """Map each anchor of the catalog to the message of its entry."""
    messages: dict[str, str] = {}
    anchor = None
    for line in catalog.splitlines():
        entry = _ENTRY.match(line)
        if entry:
            anchor = entry.group(1)
            continue
        message = _MESSAGE.match(line)
        if message and anchor:
            messages[anchor] = message.group(2)
            anchor = None
    return messages


def add_titles(page_html: str, messages: dict[str, str]) -> str:
    """Give every link to a catalog entry the entry's message as its title."""

    def titled(link: re.Match[str]) -> str:
        attrs = link.group(1)
        href = _HREF.search(attrs)
        if href is None or "title=" in attrs or href.group(1) not in messages:
            return link.group(0)
        code = href.group(1)
        title = html.escape(f"{code.upper()}: {messages[code]}", quote=True)
        return f'<a {attrs} title="{title}">'

    return _LINK.sub(titled, page_html)


def on_config(config):
    path = Path(config["docs_dir"]) / CATALOG
    _messages.clear()
    _messages.update(read_messages(path.read_text(encoding="utf-8")))
    return config


def on_page_content(page_html, page, config, files):
    return add_titles(page_html, _messages)
