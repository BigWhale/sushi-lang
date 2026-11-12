"""The declaration round of a global makes a global, or it is an internal error (ir.md 8.2).

A constant that the evaluator gives no value, a constant with no type, and a scalar of a type that has no LLVM constant, are internal
errors. No case drops the global in silence. The round reads each global from its record in
the declaration table (ir.md 6.3), which holds the folded value. The tests call the round
over a stand-in for the codegen and a record built by hand; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.types.core import LLVMTypeSystem
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.const_eval import ScalarConstant
from sushi_lang.semantics.decl_table import GlobalDecl
from sushi_lang.semantics.typesys import BuiltinType

SPAN = Span(3, 1, 3, 20)


def _codegen() -> SimpleNamespace:
    """A stand-in for the codegen. The round reads the value and the declared type of a
    record through the real `_value_of_global` and `_declared_type`."""
    return SimpleNamespace(types=LLVMTypeSystem(),
                           _value_of_global=LLVMCodegen._value_of_global,
                           _declared_type=LLVMCodegen._declared_type)


def _const(ty=BuiltinType.I32) -> GlobalDecl:
    """A record whose initializer has no folded value."""
    return GlobalDecl(name="F", unit="u", symbol="u$F", data_name="u$.str_data.F", ty=ty,
                      is_var=False, link_symbol=None, empty=False, value=None, loc=SPAN)


NO_VALUE = {"detail": "a constant with no value reached the backend"}


def test_a_constant_with_no_value_is_an_internal_error() -> None:
    with pytest.raises(InternalCompilerError) as caught:
        LLVMCodegen._emit_global_constant(_codegen(), _const(),  # type: ignore[arg-type]
                                          filename="f.sushi")
    assert caught.value.params == NO_VALUE
    assert caught.value.span == SPAN


def test_a_unit_variable_with_no_value_is_an_internal_error() -> None:
    with pytest.raises(InternalCompilerError) as caught:
        LLVMCodegen._emit_unit_variable(_codegen(), _const(),  # type: ignore[arg-type]
                                        BuiltinType.I32, True, "f.sushi")
    assert caught.value.params == NO_VALUE


def test_a_constant_with_no_type_is_an_internal_error() -> None:
    with pytest.raises(InternalCompilerError):
        LLVMCodegen._emit_global_constant(_codegen(), _const(ty=None),  # type: ignore[arg-type]
                                          filename="f.sushi")


def test_a_scalar_with_no_llvm_constant_is_an_internal_error() -> None:
    codegen = _codegen()
    with pytest.raises(InternalCompilerError):
        LLVMCodegen._materialize_constant(codegen, ScalarConstant(0, BuiltinType.BLANK),  # type: ignore[arg-type]
                                          "d", SPAN, "f.sushi")
