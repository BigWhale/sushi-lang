"""T1.3 gate: the shared `owns_resource` ownership predicate.

Every call states `drops` -- the set of type names that implement `Drop` -- because
the predicate has no default for it (HANDLES.md ruling R2a): a forgotten argument
answers False for every handle, and that is a leaked descriptor with no diagnostic.
These cases are the STRUCTURAL half, so the set is empty.
"""
from __future__ import annotations

from sushi_lang.semantics.typesys import (
    owns_resource, FunctionType, DynamicArrayType, BuiltinType,
)
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import Param

I32 = BuiltinType.I32
NO_DROPS: frozenset[str] = frozenset()


def _fn(captures=None) -> FunctionType:
    return FunctionType(param_types=(I32,), ok_type=I32, err_type=I32, captures=captures)


def test_none_is_not_owning() -> None:
    assert owns_resource(None, NO_DROPS) is False


def test_primitive_is_not_owning() -> None:
    assert owns_resource(I32, NO_DROPS) is False


def test_dynamic_array_is_owning() -> None:
    assert owns_resource(DynamicArrayType(base_type=I32), NO_DROPS) is True


def test_list_and_own_are_owning() -> None:
    assert owns_resource(GenericTypeRef(base_name="List", type_args=[I32]), NO_DROPS) is True
    assert owns_resource(GenericTypeRef(base_name="Own", type_args=[I32]), NO_DROPS) is True


def test_captures_is_tri_state_and_unstated_means_owning() -> None:
    """`captures` distinguishes "no captures" from "captures not stated"."""
    caps = (Param(name="x", ty=I32, loc=None),)
    assert owns_resource(_fn(captures=caps), NO_DROPS) is True   # known capturing
    assert owns_resource(_fn(captures=None), NO_DROPS) is True    # unstated -> assume owning
    assert owns_resource(_fn(captures=()), NO_DROPS) is False     # known empty -> owns nothing
