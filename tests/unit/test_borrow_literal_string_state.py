"""Option B: a literal-bound string owns no heap, and the binding knows it.

**Why the fact lives on the binding and not on the type.** A closure's answer lives in
`FunctionType.captures` because `FunctionType` is a dataclass with room for a field.
`BuiltinType.STRING` is an enum member with nowhere to put a flag, so the same question has to
be answered on `BorrowState` instead. The asymmetry is structural, not an oversight.

**Why it matters.** After Phase 9 a `string` MOVES, so using one after a consuming use is
CE2405. A string bound from a literal points into `.rodata` and carries `owned = 0`, so it owns
nothing and a consuming use transfers nothing -- reporting CE2405 on it would state a transfer
that did not happen. The diagnostic would be false, not merely strict. Rust
(`&'static str` vs `String`), C++ (`const char*` vs `std::string`) and Zig all draw the line in
this same place.

The default is False, meaning "assume it owns heap". That is the safe direction and the same
fallback an unstated `captures` takes.
"""
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
    """The safe default. An unstated answer must mean 'assume it owns heap'.

    If this ever flips to True-by-default, every string binding claims to own nothing and
    every consuming use of one silently aliases -- a double free, not a leak.
    """
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
    """`"hi {name}"` builds a heap buffer at runtime.

    This is the case that must never be classified as owning nothing: doing so would skip a
    real free. `InterpolatedString` is a DIFFERENT AST node from `StringLit`, which is what
    makes the distinction exact instead of a heuristic.
    """
    interp = InterpolatedString(loc=None, parts=["hi ", Name(loc=None, id="name")])
    assert binds_a_bare_literal_string(BuiltinType.STRING, interp) is False


@pytest.mark.parametrize("init", [
    Name(loc=None, id="other"),
    MemberAccess(loc=None, receiver=Name(loc=None, id="s"), member="name"),
    InterpolatedString(loc=None, parts=["a"]),
], ids=["name", "field_read", "interpolation_with_no_expressions"])
def test_every_other_initializer_shape_is_owning(init):
    """An ALLOW-LIST, not a deny-list.

    Only a `StringLit` answers True. A deny-list ("anything that is not an
    InterpolatedString") would answer True for a name, a field read, a call and a get-out --
    every one of which can name a buffer someone else owns.

    Note the third case: an `InterpolatedString` with no interpolated expressions is still not
    a `StringLit`, so it answers False. Conservative, and correct.
    """
    assert binds_a_bare_literal_string(BuiltinType.STRING, init) is False


def test_a_non_string_type_is_never_exempt():
    """The exemption is about the `owned` bit of a string, not about literals in general."""
    arr = DynamicArrayType(base_type=BuiltinType.I32)
    assert binds_a_bare_literal_string(arr, StringLit(loc=None, value="hi")) is False
    assert binds_a_bare_literal_string(BuiltinType.I32, StringLit(loc=None, value="hi")) is False
