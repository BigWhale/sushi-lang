"""Option B: a literal-bound string owns no heap, and the binding knows it."""
from __future__ import annotations

import pytest

from sushi_lang.semantics.ast import (
    InterpolatedString,
    MemberAccess,
    Name,
    StringLit,
)
from sushi_lang.semantics.passes.borrow import BorrowState, binds_a_bare_literal_string
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType


def test_default_is_owning():
    """The safe default. An unstated answer must mean 'assume it owns heap'."""
    assert BorrowState(name="s").owns_no_heap is False


def test_field_is_settable():
    assert BorrowState(name="s", owns_no_heap=True).owns_no_heap is True


# ---------------------------------------------------------------------------
# The rule itself. `binds_a_bare_literal_string` is its single spelling -- the `let` path
# and the rebind path both call it, so pinning it here pins both.
# ---------------------------------------------------------------------------

def test_a_bare_literal_owns_nothing():
    """The whole point of option B: `let string s = "hi"` must not be a MOVE."""
    assert binds_a_bare_literal_string(BuiltinType.STRING, StringLit(loc=None, value="hi")) is True


def test_an_interpolation_owns_its_buffer():
    """`"hi {name}"` builds a heap buffer at runtime."""
    interp = InterpolatedString(loc=None, parts=["hi ", Name(loc=None, id="name")])
    assert binds_a_bare_literal_string(BuiltinType.STRING, interp) is False


@pytest.mark.parametrize("init", [
    Name(loc=None, id="other"),
    MemberAccess(loc=None, receiver=Name(loc=None, id="s"), member="name"),
    InterpolatedString(loc=None, parts=["a"]),
], ids=["name", "field_read", "interpolation_with_no_expressions"])
def test_every_other_initializer_shape_is_owning(init):
    """An ALLOW-LIST, not a deny-list."""
    assert binds_a_bare_literal_string(BuiltinType.STRING, init) is False


def test_a_non_string_type_is_never_exempt():
    """The exemption is about the `owned` bit of a string, not about literals in general."""
    arr = DynamicArrayType(base_type=BuiltinType.I32)
    assert binds_a_bare_literal_string(arr, StringLit(loc=None, value="hi")) is False
    assert binds_a_bare_literal_string(BuiltinType.I32, StringLit(loc=None, value="hi")) is False
