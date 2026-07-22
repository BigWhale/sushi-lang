"""#236: `.debug()` headers render generic types through the display layer.

`List.debug()` / `HashMap.debug()` build their header f-string in the backend. A
monomorphized type's `str()` is its interned `<...>` identity name, so
interpolating the element/key/value type directly renders a *nested* generic in
the retired syntax (`List@(List<i32>)`). The header must go through
`display_type`, which recurses on `generic_base`/`generic_args`.

The source assertions are the guard: the rendering bug is invisible for a flat
element type (`str(i32) == "i32"`), so a regression would slip past every
existing debug test that does not nest.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sushi_lang.backend.generics.hashmap.methods import debug as hashmap_debug
from sushi_lang.backend.generics.list import methods_debug as list_debug
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import BuiltinType, StructType

I32 = BuiltinType.I32
STRING = BuiltinType.STRING

# Every `@(`-bearing header f-string in the two emitters, as (module, source) pairs.
EMITTERS = [
    (list_debug, Path(list_debug.__file__).read_text()),
    (hashmap_debug, Path(hashmap_debug.__file__).read_text()),
]

# An f-string placeholder holding a bare name, e.g. `{element_type}` -- the bug shape.
_BARE_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _header_lines(source: str) -> list[str]:
    """The `header_str = f"...@(...)..."` assignment lines."""
    return [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("header_str") and "@(" in line
    ]


@pytest.mark.parametrize("module,source", EMITTERS, ids=lambda v: getattr(v, "__name__", "src"))
def test_header_routes_through_display_type(module, source):
    headers = _header_lines(source)
    assert headers, f"no `@(` debug header found in {module.__name__}"
    for header in headers:
        assert "display_type(" in header, f"header bypasses the display layer: {header}"
        assert not _BARE_PLACEHOLDER.search(header), (
            f"header interpolates a bare type via str(): {header}"
        )


def test_nested_generic_display_shape():
    """The shape the headers must produce -- what #236 reported wrong."""
    inner = StructType(name="List<i32>", fields=[], generic_base="List", generic_args=(I32,))
    assert display_type(inner) == "List@(i32)"
    assert f"List@({display_type(inner)})" == "List@(List@(i32))"
    assert f"HashMap@({display_type(STRING)}, {display_type(inner)})" == (
        "HashMap@(string, List@(i32))"
    )


def test_flat_element_rendering_is_unchanged():
    """The no-regression invariant: a non-generic type displays exactly as str()."""
    for ty in (I32, STRING, BuiltinType.BOOL, BuiltinType.F64):
        assert display_type(ty) == str(ty)
