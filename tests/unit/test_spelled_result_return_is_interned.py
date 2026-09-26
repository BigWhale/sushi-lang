"""A spelled `Result@(T, E)` return reaches the backend as the INTERNED enum (#857).

The `resolve` pass interns the spelled return through `intern_wrapper_enum` and stamps
it on `FuncDef.resolved_result`. The declaration keeps the type as written, because the
typecheck pass still has to rule on a qualified name in it. The backend then has one
reader, `declared_result_of`, and the prototype has no structural arm of its own.
"""
from __future__ import annotations

import dataclasses
import inspect
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sushi_lang.backend.functions import declarations, helpers
from sushi_lang.internals.errors import InternalCompilerError
from sushi_lang.semantics.ast import FuncDef
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType, EnumType, UnknownType

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_metadata import collect_fixtures  # noqa: E402

SPELLED_HEADER = re.compile(r"^\s*(public\s+)?fn\s+\w+\(.*\)\s+Result@\(", re.MULTILINE)








def test_the_prototype_has_no_spelled_result_arm():
    source = inspect.getsource(declarations)
    assert "GenericTypeRef" not in source
    assert "prototype_result_of" not in source


def test_the_one_reader_has_no_spelled_result_arm():
    assert "GenericTypeRef" not in inspect.getsource(helpers.declared_result_of)


@pytest.mark.parametrize("node", [FuncDef, GenericFuncDef])
def test_every_function_node_the_backend_declares_carries_the_stamp(node):
    # A monomorphized instance is a copy of a GenericFuncDef, and the backend declares
    # it through the same reader.
    assert "resolved_result" in {f.name for f in dataclasses.fields(node)}


def test_the_reader_answers_the_stamp():
    interned = EnumType(name="Result<i32, E>", variants=(), generic_base="Result")
    spelled = GenericTypeRef(base_name="Result",
                             type_args=(BuiltinType.I32, UnknownType(name="E")))
    fn = SimpleNamespace(name="f", ret=spelled, err_type=None, resolved_result=interned)
    assert helpers.declared_result_of(object(), fn) is interned


def test_an_unstamped_spelled_return_is_an_internal_error():
    spelled = GenericTypeRef(base_name="Result",
                             type_args=(BuiltinType.I32, UnknownType(name="E")))
    fn = SimpleNamespace(name="f", ret=spelled, err_type=None, resolved_result=None)
    with pytest.raises(InternalCompilerError) as caught:
        helpers.declared_result_of(object(), fn)
    assert "CE0015" in str(caught.value)








def _single_unit_fixtures_that_spell_a_result_return():
    for fixture in collect_fixtures(TESTS_DIR):
        if fixture.name.startswith(("test_err_", "test_warn_")):
            continue
        text = fixture.read_text(encoding="utf-8")
        if 'use "' in text or not SPELLED_HEADER.search(text):
            continue
        yield fixture


CORPUS = list(_single_unit_fixtures_that_spell_a_result_return())


def test_the_corpus_has_spelled_returns_to_check():
    assert len(CORPUS) >= 5


