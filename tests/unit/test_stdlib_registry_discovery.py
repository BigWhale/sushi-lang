"""Stdlib module discovery must be loud, and every entry must register (#247).

A module listed in KNOWN_MODULES whose python module does not import, or whose
three interface symbols are absent, is a compiler configuration error -- not a
no-op. The silent-skip path is what let two commented "just uncomment when
ready" entries sit for months while the real blocker was an interface
mismatch.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.stdlib_registry import StdlibRegistry


def test_every_known_module_registers_functions():
    """No entry may silently register nothing."""
    registry = StdlibRegistry()
    registry.discover_modules()
    for module_path in StdlibRegistry.KNOWN_MODULES:
        module = registry.get_module(module_path)
        assert module is not None, f"'{module_path}' registered no module"
        assert module.functions or module.constants, (
            f"'{module_path}' registered no functions and no constants -- "
            "the discovery silently skipped it"
        )


def test_missing_interface_symbols_raise():
    """A module that imports fine but lacks the three symbols must be loud.

    sushi_stdlib.src.collections.strings is the real-world case: it exposes a
    METHOD interface (is_builtin_string_method), not the function interface the
    registry reads -- which is exactly why a KNOWN_MODULES entry for it could
    never have worked.

    io/stdio used to be the example here, and was the better one because its
    entry had actually been tried. It was retired in HANDLES.md Phase 5, when the
    console handles became File constants.
    """
    registry = StdlibRegistry()
    with pytest.raises(RuntimeError, match="strings"):
        registry._discover_module(
            "collections/strings", "sushi_lang.sushi_stdlib.src.collections.strings")


def test_unimportable_module_raises():
    """A python path that does not import must be loud, not skipped."""
    registry = StdlibRegistry()
    registry.KNOWN_MODULES = dict(StdlibRegistry.KNOWN_MODULES)
    registry.KNOWN_MODULES["bogus"] = "sushi_lang.no_such_module"
    with pytest.raises((RuntimeError, ImportError)):
        registry.discover_modules()
