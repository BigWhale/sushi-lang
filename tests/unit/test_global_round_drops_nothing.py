"""The declaration round of a global makes a global, or it stops the compile (ir.md 8.2).

A constant that the evaluator gives no value stops with `emit, constant with no value`. A
constant with no type, and a scalar of a type that has no LLVM constant, are internal
errors. No case drops the global in silence. The tests call the round over a stand-in for
the codegen; they do not run the compiler.
"""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace

import pytest

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.types.core import LLVMTypeSystem
from sushi_lang.internals.diagnostics import InternalCompilerError, StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.const_eval import ScalarConstant
from sushi_lang.semantics.typesys import BuiltinType

SPAN = Span(3, 1, 3, 20)


def _codegen(value) -> SimpleNamespace:
    """A stand-in for the codegen whose evaluator gives `value` for each initializer. The
    round reads the value through the real `_value_of_global`, and the declared type
    through the real `_declared_type`, from a constant table that holds the stand-in's
    type."""
    codegen = SimpleNamespace(_evaluate_constant_expression=lambda *_args: value,
                              types=LLVMTypeSystem(),
                              const_table=SimpleNamespace(
                                  record_of=lambda decl, _unit: SimpleNamespace(
                                      const_type=decl.ty)))
    codegen._value_of_global = partial(LLVMCodegen._value_of_global, codegen)
    codegen._declared_type = partial(LLVMCodegen._declared_type, codegen)
    return codegen


def _const(ty=BuiltinType.I32) -> SimpleNamespace:
    return SimpleNamespace(name="F", ty=ty, value=SimpleNamespace(), loc=SPAN,
                           scope_unit=None, home_unit=None, link_symbol=None)


def test_a_constant_with_no_value_stops() -> None:
    with pytest.raises(StageStop) as caught:
        LLVMCodegen._emit_global_constant(_codegen(None), _const(), "u",  # type: ignore[arg-type]
                                          filename="f.sushi")
    assert caught.value.params == {"stage": "emit", "construct": "constant with no value"}
    assert caught.value.span == SPAN


def test_a_unit_variable_with_no_value_stops() -> None:
    with pytest.raises(StageStop) as caught:
        LLVMCodegen._emit_unit_variable(_codegen(None), _const(),  # type: ignore[arg-type]
                                        BuiltinType.I32, "u", True, "f.sushi")
    assert caught.value.params == {"stage": "emit", "construct": "constant with no value"}


def test_a_constant_with_no_type_is_an_internal_error() -> None:
    with pytest.raises(InternalCompilerError):
        LLVMCodegen._emit_global_constant(_codegen(None), _const(ty=None), "u",  # type: ignore[arg-type]
                                          filename="f.sushi")


def test_a_scalar_with_no_llvm_constant_is_an_internal_error() -> None:
    codegen = _codegen(None)
    with pytest.raises(InternalCompilerError):
        LLVMCodegen._materialize_constant(codegen, ScalarConstant(0, BuiltinType.BLANK),  # type: ignore[arg-type]
                                          "d", SPAN, "f.sushi")
