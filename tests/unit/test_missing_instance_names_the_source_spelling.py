"""CE0045 names a missing generic instance in the source spelling, never the interned one.

The internal error reaches the user, so its text obeys the spelling gate: `(i32, i32)`
and `Maybe@(i32)`, not `$Tuple<i32, i32>` and `Maybe<i32>`. Each of the two seams that
look an instance up by its interned name is asked here with tables that hold nothing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.backend.types.core.resolution import require_generic_instance
from sushi_lang.internals.errors import InternalCompilerError, message_for
from sushi_lang.internals.report import SPELLING_GATE_ENV, check_spelling
from sushi_lang.semantics.generics.tuples import tuple_ref
from sushi_lang.semantics.generics.type_strings import resolve_type_from_string
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType


@pytest.fixture(autouse=True)
def armed(monkeypatch):
    monkeypatch.setenv(SPELLING_GATE_ENV, "1")


def _rendered(raised: pytest.ExceptionInfo) -> str:
    assert raised.value.code == "CE0045"
    text = message_for("CE0045", **raised.value.params)
    check_spelling("CE0045", text)
    return text


@pytest.mark.parametrize("missing, spelled", [
    (tuple_ref((BuiltinType.I32, BuiltinType.I32)), "(i32, i32)"),
    (GenericTypeRef("Maybe", (BuiltinType.I32,)), "Maybe@(i32)"),
])
def test_a_missing_instance_reference_reads_as_written(missing, spelled):
    with pytest.raises(InternalCompilerError) as raised:
        require_generic_instance(missing, {}, {})
    assert f"'{spelled}'" in _rendered(raised)


@pytest.mark.parametrize("missing, spelled", [
    ("$Tuple<i32, string>", "(i32, string)"),
    ("Result<i32, MathError>", "Result@(i32, MathError)"),
])
def test_a_missing_instance_name_reads_as_written(missing, spelled):
    tables = SimpleNamespace(enum_table=SimpleNamespace(by_name={}),
                             struct_table=SimpleNamespace(by_name={}))
    with pytest.raises(InternalCompilerError) as raised:
        resolve_type_from_string(missing, tables)
    assert f"'{spelled}'" in _rendered(raised)
