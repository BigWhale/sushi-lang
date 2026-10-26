""""This method changes its receiver" is ONE fact with ONE source (#791 row 5).

The array table in the typecheck pass kept its own `mutates` flag to refuse an in-place
method on a constant (CE2096), and the borrow pass kept `METHOD_EFFECTS` for the same
fact. The two disagreed on `push`, `pop`, `destroy` and `free`, and the disagreement WAS
reachable: `clear()` on a constant fixed array read CE2096 while `push(4)` read CE2023.
The table now lives in `semantics/method_effects.py`, outside both passes, and the array
check reads it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sushi_lang.semantics.method_effects import METHOD_EFFECTS, effect_of
from sushi_lang.semantics.passes.types import arrays

ARRAYS = Path(arrays.__file__)


def test_the_array_row_carries_no_flag_of_its_own():
    assert "mutates" not in {f.name for f in dataclasses.fields(arrays.ArraySpec)}


def test_the_typecheck_pass_does_not_import_the_borrow_pass():
    assert "passes.borrow" not in ARRAYS.read_text()


def test_the_borrow_pass_reads_the_same_table():
    from sushi_lang.semantics.passes.borrow import methods

    assert methods.METHOD_EFFECTS is METHOD_EFFECTS


def test_every_array_method_the_table_marks_is_measured():
    """The control: the array rows the table marks as mutating are not an empty set."""
    marked = {name for name in arrays._ARRAY_METHODS if effect_of(name).mutates}
    assert {"clear", "push", "pop", "fill", "reverse", "extend",
            "extend_back"} <= marked


def test_a_family_reads_its_receiver_or_has_effect_rows_never_both():
    """A family whose methods only read their receiver has no row of `METHOD_EFFECTS`, and
    a family that the effect rows describe does not only read its receiver. Each name of
    the two sets is a family of `METHOD_TYPE_REGISTRY`."""
    from sushi_lang.semantics.method_effects import EFFECT_FAMILIES, RECEIVER_READ_FAMILIES
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

    registry = {family.name for family in METHOD_TYPE_REGISTRY.families}
    assert RECEIVER_READ_FAMILIES, "no family reads its receiver"
    assert RECEIVER_READ_FAMILIES.isdisjoint(EFFECT_FAMILIES)
    unknown = (RECEIVER_READ_FAMILIES | EFFECT_FAMILIES) - registry
    assert not unknown, f"no family of METHOD_TYPE_REGISTRY is named {sorted(unknown)}"


def test_the_borrow_pass_and_slir_lowering_read_the_one_set_of_read_receivers():
    from sushi_lang.semantics import method_effects
    from sushi_lang.semantics.passes.borrow import calls
    from sushi_lang.semantics.slir import lower

    assert calls.RECEIVER_READ_FAMILIES is method_effects.RECEIVER_READ_FAMILIES
    assert lower.RECEIVER_READ_FAMILIES is method_effects.RECEIVER_READ_FAMILIES


def test_slir_lowering_gives_a_read_receiver_a_borrow_and_refuses_any_other_family():
    """A receiver of another family can be consumed (`or_err`, `map_err`), so a borrow
    there would be a silent fallback. Lowering gets no such call while the family has no
    arm, and one that comes is an internal error."""
    from sushi_lang.internals.diagnostics import InternalCompilerError
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.method_effects import RECEIVER_READ_FAMILIES
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.slir import lower

    span = Span(1, 1, 1, 2)
    for family in RECEIVER_READ_FAMILIES:
        assert lower.receiver_convention(family, span) is Convention.GUARANTEED
    with pytest.raises(InternalCompilerError) as caught:
        lower.receiver_convention("maybe", span)
    assert caught.value.code == "CE0000"
