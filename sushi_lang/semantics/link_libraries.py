"""The C libraries that a program asks the linker for: one `LinkRequest` for each name of
each `link` clause.

The `externs` pass makes the requests of every unit that has an AST, the bundled stdlib
units and the units of a source library included. The back end makes the link plan from
them (`backend/link_plan.py`). The rules are in `docs/design/libraries.md`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from sushi_lang.internals.report import Span

_FILE_SUFFIXES = (".so", ".dylib", ".a")


@dataclass(frozen=True)
class LinkRequest:
    """One library name, and where it was asked for: a clause of the unit `unit`
    (`span`, `filename`)."""
    library: str
    span: Optional[Span] = None
    filename: Optional[str] = None
    unit: Optional[str] = None


def invalid_reason(name: str) -> Optional[str]:
    """Why `name` cannot go to the linker as `-l<name>` (CE5017), or None."""
    if not name:
        return "the name is empty"
    if "/" in name:
        return "a name holds no '/'"
    if any(ch.isspace() for ch in name):
        return "a name holds no white space"
    if name.startswith("-"):
        return "a name does not start with '-'"
    return None


def library_of_file_name(name: str) -> Optional[str]:
    """The library name that the file name `name` spells (CE5018), or None when `name` is
    a library name: `libz.so` gives `z`."""
    stem = name
    for suffix in _FILE_SUFFIXES:
        at = stem.find(suffix + ".") if suffix == ".so" else -1
        if at > 0:
            stem = stem[:at]
        elif stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[:-len(suffix)]
    if stem.startswith("lib") and len(stem) > 3:
        stem = stem[3:]
    return None if stem == name else stem


def library_names(requests: Iterable[LinkRequest]) -> list[str]:
    """The names of `requests`, each one time, in the order of the first appearance."""
    return list(dict.fromkeys(request.library for request in requests))
