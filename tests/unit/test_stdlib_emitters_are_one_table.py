"""The backend picks a registry stdlib call's emitter from ONE table.

A closed set of choices is one table, so a new module adds one row. Every module
path the semantic side can answer a free function for has a row in
`STDLIB_EMITTERS`, and no row names a module the semantic side does not know: a
module the registry answers and the table misses crashes as CE0055 in the backend.
"""
from __future__ import annotations

import inspect

from sushi_lang.backend.expressions.calls import dispatcher
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


def test_the_dispatcher_holds_no_module_path_ladder():
    source = inspect.getsource(dispatcher._emit_stdlib_function)
    assert "module_path ==" not in source
    assert "STDLIB_EMITTERS" in source
