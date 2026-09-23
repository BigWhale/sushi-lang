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
    assert {"clear", "push", "pop", "fill", "reverse", "extend"} <= marked


_CONSTANT = "const i32[3] A = [1, 2, 3]\n\nfn main() i32:\n    A.{call}\n    return Result.Ok(0)\n"


@pytest.mark.parametrize("call", [
    name + ("(4)" if spec.arity == 1 else "(0, 1)" if spec.arity == 2
            else "(from([9]), 0, 1)" if spec.arity == 3 else "()")
    for name, spec in arrays._ARRAY_METHODS.items() if effect_of(name).mutates
])
def test_a_mutating_method_on_a_constant_reads_ce2096(analyze, call):
    codes = [item.code for item in analyze(_CONSTANT.format(call=call), name="m").items]
    assert codes == ["CE2096"], (call, codes)
