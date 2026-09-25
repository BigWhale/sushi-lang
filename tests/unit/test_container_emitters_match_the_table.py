"""The List and HashMap emitter tables hold the names of the semantic arity tables (#875).

The semantic arity table is the one list of a container's method names. The backend holds
one `name -> emitter` row per method, and the two key sets must be equal in both
directions: a method in the arity table with no row reaches the backend with no emitter,
and a row with no arity entry is a method no program can call. This gate may import both
layers; the compiler code may not.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.backend.generics.hashmap import HASHMAP_EMITTERS
from sushi_lang.backend.generics.list import LIST_EMITTERS
from sushi_lang.semantics.generics.hashmap import HASHMAP_METHOD_ARITY
from sushi_lang.semantics.generics.list import LIST_METHOD_ARITY
from sushi_lang.semantics.statics import BUILTIN_STATICS

_TABLES = [
    pytest.param(LIST_EMITTERS, LIST_METHOD_ARITY, "List", id="List"),
    pytest.param(HASHMAP_EMITTERS, HASHMAP_METHOD_ARITY, "HashMap", id="HashMap"),
]

_DISPATCH = (Path(__file__).resolve().parents[2]
             / "sushi_lang" / "backend" / "expressions" / "calls" / "generics.py")


@pytest.mark.parametrize("emitters, arity, base", _TABLES)
def test_every_arity_name_has_an_emitter(emitters, arity, base):
    assert set(arity) - set(emitters) == set()


@pytest.mark.parametrize("emitters, arity, base", _TABLES)
def test_every_emitter_has_an_arity_name(emitters, arity, base):
    assert set(emitters) - set(arity) == set()


@pytest.mark.parametrize("emitters, arity, base", _TABLES)
def test_every_row_is_an_emitter_and_a_bool_flag(emitters, arity, base):
    for name, row in emitters.items():
        assert callable(row.emit), name
        assert isinstance(row.answers_bool, bool), name


@pytest.mark.parametrize("emitters, arity, base", _TABLES)
def test_the_statics_are_container_methods(emitters, arity, base):
    assert BUILTIN_STATICS[base] <= set(emitters)


def test_the_dispatcher_does_not_write_the_static_names_again():
    tree = ast.parse(_DISPATCH.read_text())
    statics = set().union(BUILTIN_STATICS["List"], BUILTIN_STATICS["HashMap"])
    written = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and node.value in statics]
    assert written == []
