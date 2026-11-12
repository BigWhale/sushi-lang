"""The backend picks a registry stdlib call's emitter from ONE table.

A closed set of choices is one table, so a new module adds one row. Every module
path the semantic side can answer a free function for has a row in
`STDLIB_EMITTERS`, and no row names a module the semantic side does not know: a
module the registry answers and the table misses is the internal error CE0000 in the
emitter (`backend/slir/emit.py`, `_stdlib`).
"""
from __future__ import annotations

from sushi_lang.backend.expressions.calls.stdlib import STDLIB_EMITTERS
from sushi_lang.semantics.stdlib_registry import (
    StdlibRegistry, get_stdlib_registry, signature_tables)


def _semantic_modules() -> set[str]:
    registry = get_stdlib_registry()
    answered = {path for path in registry.get_all_modules()
                if registry.get_module(path).functions}
    return answered | set(signature_tables()) | set(StdlibRegistry.KNOWN_MODULES)


def test_every_semantic_module_has_an_emitter_row():
    assert _semantic_modules() - set(STDLIB_EMITTERS) == set()


def test_no_emitter_row_names_an_unknown_module():
    assert set(STDLIB_EMITTERS) - _semantic_modules() == set()


def test_every_row_is_callable():
    for path, emit in STDLIB_EMITTERS.items():
        assert callable(emit), path

