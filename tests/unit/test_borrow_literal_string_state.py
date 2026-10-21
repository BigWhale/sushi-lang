"""Option B: a literal-bound string owns no heap, and the binding knows it.

The borrow pass reads the value of a `let` and of a rebind as a SHIR node
(docs/design/ir.md 7.4, 7.9): a `Lit` of a text owns no heap, and an `Interp` builds a
heap buffer at run time.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import BorrowState, binds_a_bare_literal_string
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType

_AT = Span(1, 1, 1, 2)


def _text(value: str) -> e.Lit:
    return e.Lit(ShirId(0), _AT, e.StrConst(value))


def _local(index: int) -> e.LocalRef:
    return e.LocalRef(ShirId(1), _AT, LocalId(index))


def test_default_is_owning():
    """The safe default. An unstated answer must mean 'assume it owns heap'."""
    assert BorrowState(name="s").owns_no_heap is False


def test_field_is_settable():
    assert BorrowState(name="s", owns_no_heap=True).owns_no_heap is True


# The rule itself. `binds_a_bare_literal_string` is its single spelling -- the `let` path
# and the rebind path both call it, so pinning it here pins both.

def test_a_bare_literal_owns_nothing():
    """The whole point of option B: `let string s = "hi"` must not be a MOVE."""
    assert binds_a_bare_literal_string(BuiltinType.STRING, _text("hi")) is True


def test_an_interpolation_owns_its_buffer():
    """`"hi {name}"` builds a heap buffer at runtime."""
    interp = e.Interp(ShirId(2), _AT, ["hi ", _local(0)])
    assert binds_a_bare_literal_string(BuiltinType.STRING, interp) is False


@pytest.mark.parametrize("init", [
    _local(0),
    e.Field(ShirId(3), _AT, _local(0), "name"),
    e.Interp(ShirId(4), _AT, ["a"]),
    e.Lit(ShirId(5), _AT, e.IntConst(5)),
], ids=["local", "field_read", "interpolation_with_no_expressions", "integer_literal"])
def test_every_other_initializer_shape_is_owning(init):
    """An ALLOW-LIST, not a deny-list."""
    assert binds_a_bare_literal_string(BuiltinType.STRING, init) is False


def test_a_non_string_type_is_never_exempt():
    """The exemption is about the `owned` bit of a string, not about literals in general."""
    arr = DynamicArrayType(base_type=BuiltinType.I32)
    assert binds_a_bare_literal_string(arr, _text("hi")) is False
    assert binds_a_bare_literal_string(BuiltinType.I32, _text("hi")) is False
