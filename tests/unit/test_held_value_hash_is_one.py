"""The hash of a held value is ONE function: `emit_value_hash` (#871).

A struct field, an enum payload, an array element, a `List@(T)` element and an
`Own@(T)` payload each had a copy of "hash a value of type T". The copies accepted
different type sets (the element copy refused an array, CE0052) and none of them used a
`Hashable` override. This gate keeps the lookup in one module: only
`backend/types/value_hash.py` reads the derived `hash` of a held value, and the holder
emitters call it and read no hash method of their own.
"""
from __future__ import annotations

import ast
from pathlib import Path

from sushi_lang.backend.types import value_hash

BACKEND = Path(value_hash.__file__).resolve().parents[1]

THE_SEAM = "types/value_hash.py"

# A map KEY is looked up at its own seam, `get_key_hash_method`, which puts the perk
# implementation first in the same way. It is not a held value.
KEY_LOOKUP = "generics/hashmap/types.py"

HOLDER_EMITTERS = (
    "types/structs.py",
    "types/enums.py",
    "types/arrays/methods/hashing.py",
    "generics/hashing.py",
)


def _hash_lookups(source: str) -> int:
    """The calls `<x>.derived_methods.get_method(<type>, "hash")` in one module."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get_method"):
            continue
        owner = func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "derived_methods"):
            continue
        if any(isinstance(a, ast.Constant) and a.value == "hash" for a in node.args):
            count += 1
    return count


def _modules() -> dict[str, str]:
    return {
        path.relative_to(BACKEND).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(BACKEND.rglob("*.py"))
    }


def test_only_the_seam_reads_a_derived_hash():
    readers = {name for name, source in _modules().items() if _hash_lookups(source)}
    assert readers - {THE_SEAM, KEY_LOOKUP} == set(), sorted(readers)
    assert THE_SEAM in readers


def test_the_holder_emitters_ask_the_seam():
    modules = _modules()
    for name in HOLDER_EMITTERS:
        source = modules[name]
        assert "emit_value_hash" in source, name
        assert "llvm_emitter" not in source, name
        assert ".get_method(" not in source, name


def test_the_scan_sees_a_lookup():
    source = 'def f(codegen, t):\n    return codegen.derived_methods.get_method(t, "hash")\n'
    assert _hash_lookups(source) == 1
    assert _hash_lookups('codegen.derived_methods.get_method(t, "clone")\n') == 0
