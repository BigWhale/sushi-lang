"""T1.3 gate: the shared `owns_heap` ownership predicate (was `is_owning_type`).

Factored out of the borrow checker so the borrow pass and the backend RAII paths agree
on what owns heap memory. Every function value owns, whether or not its `captures`
metadata survived -- see `test_every_function_value_is_owning` for why.
"""
from __future__ import annotations

from sushi_lang.semantics.typesys import (
    owns_heap, FunctionType, DynamicArrayType, BuiltinType,
)
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import Param

I32 = BuiltinType.I32


def _fn(captures=None) -> FunctionType:
    return FunctionType(param_types=(I32,), ok_type=I32, err_type=I32, captures=captures)


def test_none_is_not_owning() -> None:
    assert owns_heap(None) is False


def test_primitive_is_not_owning() -> None:
    assert owns_heap(I32) is False


def test_dynamic_array_is_owning() -> None:
    assert owns_heap(DynamicArrayType(base_type=I32)) is True


def test_list_and_own_are_owning() -> None:
    assert owns_heap(GenericTypeRef(base_name="List", type_args=[I32])) is True
    assert owns_heap(GenericTypeRef(base_name="Own", type_args=[I32])) is True


def test_captures_is_tri_state_and_unstated_means_owning() -> None:
    """`captures` distinguishes "no captures" from "captures not stated".

    `FunctionType.__eq__` excludes `captures` from type identity, so a closure reaching
    a position through a DECLARED type -- a `List@(fn(i32) -> i32)` element, a struct
    field, a parameter -- arrives with `None` while still owning a heap environment.
    Reading `None` as "no captures" answered False for a real owner, and the position
    then aliased the environment: `tests/memory/test_closure_into_list.sushi` and
    `tests/memory/test_struct_closure_field_by_value.sushi` both double-freed.

    An empty tuple is different. It is a STATEMENT that there are no captures, which
    only the binding's initializer can make, so a plain fn reference stays copyable and
    does not report CE2405 on its second use.

    Reading `None` as owning is free: a non-capturing value carries a null `drop_ptr`
    and a null `clone_ptr`, so its destroy and its clone are both no-ops.
    """
    caps = (Param(name="x", ty=I32, loc=None),)
    assert owns_heap(_fn(captures=caps)) is True   # known capturing
    assert owns_heap(_fn(captures=None)) is True    # unstated -> assume owning
    assert owns_heap(_fn(captures=())) is False     # known empty -> owns nothing
