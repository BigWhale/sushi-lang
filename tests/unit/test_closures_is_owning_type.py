"""T1.3 gate: the shared `is_owning_type` ownership predicate.

Factored out of the borrow checker so the borrow pass and the backend RAII paths
agree on what owns heap memory. A capturing function value (closure) is owning; a
non-capturing one stays copyable (v1 first-class-function ergonomics).
"""
from __future__ import annotations

from sushi_lang.semantics.typesys import (
    is_owning_type, FunctionType, DynamicArrayType, BuiltinType,
)
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import Param

I32 = BuiltinType.I32


def _fn(captures=None) -> FunctionType:
    return FunctionType(param_types=(I32,), ok_type=I32, err_type=I32, captures=captures)


def test_none_is_not_owning() -> None:
    assert is_owning_type(None) is False


def test_primitive_is_not_owning() -> None:
    assert is_owning_type(I32) is False


def test_dynamic_array_is_owning() -> None:
    assert is_owning_type(DynamicArrayType(base_type=I32)) is True


def test_list_and_own_are_owning() -> None:
    assert is_owning_type(GenericTypeRef(base_name="List", type_args=[I32])) is True
    assert is_owning_type(GenericTypeRef(base_name="Own", type_args=[I32])) is True


def test_non_capturing_function_value_is_not_owning() -> None:
    # Preserves v1 ergonomics: a bare fn reference is copyable, referenceable freely.
    assert is_owning_type(_fn(captures=None)) is False
    assert is_owning_type(_fn(captures=())) is False


def test_capturing_closure_is_owning() -> None:
    caps = (Param(name="x", ty=I32, loc=None),)
    assert is_owning_type(_fn(captures=caps)) is True
