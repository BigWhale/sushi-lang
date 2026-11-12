"""The parameters of a declaration in the declaration table (docs/design/ir.md 6.3).

One rule for a free function, an extension method and a method of a perk implementation: a
parameter with no type is the internal error CE0015, because the collect pass refuses such
a declaration before the table is built. Each test calls a Python function over data and
does not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.ast import Param
from sushi_lang.semantics.decl_table import _Builder
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.typesys import BuiltinType


def _builder() -> _Builder:
    return _Builder(SymbolTables(), None)  # type: ignore[arg-type]


def test_a_typed_parameter_has_its_type():
    assert _builder().typed_params("Box.grow", [Param("n", BuiltinType.I32)]) \
        == (("n", BuiltinType.I32),)


def test_a_method_parameter_with_no_type_is_an_internal_error():
    with pytest.raises(InternalCompilerError) as caught:
        _builder().typed_params("Box.grow", [Param("n", None)])
    assert caught.value.code == "CE0015"
