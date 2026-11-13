"""The SLIR verifier over hand-built functions of arrays (docs/design/ir.md 8.7, 8.13).

The rules of an array value, of a length and a slice, of a view, and of an element that
never moves. Each test gives the per-function check one function; it calls a Python
function over data and does not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.ids import BlockId
from sushi_lang.semantics.typesys import ArrayType, BuiltinType, DynamicArrayType

from tests.unit.test_slir_verifier import (
    BAD, BLANK, BOOL, I32, SPAN, STRING, _block, _copy, _dead, _drop, _function, _live,
    _local, _move, _passes, _refused, _ret, _set,
)

U32, U64, I64 = BuiltinType.U32, BuiltinType.U64, BuiltinType.I64
GUARANTEED, OWNED = Convention.GUARANTEED, Convention.OWNED
TRIPLE = ArrayType(base_type=I32, size=3)
NUMBERS = DynamicArrayType(base_type=I32)
FLOATS = DynamicArrayType(base_type=BuiltinType.F64)

ARRAY_VALUE = "an array value"
LENGTH_SLICE = "a length and a slice"
VIEW = "a view"
ELEMENT_MOVE = "no move of an element"
TYPED_PLACES = "typed places"


def _i32(value: int) -> p.Const:
    return p.Const(k.Int(value, I32))


def _i64(value: int) -> p.Const:
    return p.Const(k.Int(value, I64))


def _array(ty, runs, *parts, span=SPAN) -> st.Assign:
    return _set(1, r.Aggregate(r.AggArray(ty, runs), parts), span)


def _done() -> st.Assign:
    return _set(0, r.Use(p.Const(k.Blank())))


# -- functions that keep every rule ----------------------------------------------------


def test_a_fixed_array_of_runs_whose_constant_counts_add_up_passes():
    runs = (r.RunOne(), r.RunRepeat(), r.RunSequence(-1))
    _passes(_function([BLANK, TRIPLE, I32], _block(
        0, _ret(), _live(1),
        _array(TRIPLE, runs, _copy(2), p.Const(k.Int(7, I32)), _i64(1), _i32(9), _i32(1)),
        _done(), _dead(1)), params=((2, GUARANTEED),)))


def test_a_dynamic_array_of_run_time_counts_drops_and_passes():
    runs = (r.RunRepeat(), r.RunSequence(1))
    _passes(_function([BLANK, NUMBERS, I32, I64], _block(
        0, _ret(), _live(1), _array(NUMBERS, runs, _i32(0), _copy(3), _copy(2), _copy(2)),
        _done(), _drop(1), _dead(1)), params=((2, GUARANTEED), (3, GUARANTEED))))


def test_an_empty_dynamic_array_passes():
    _passes(_function([BLANK, NUMBERS], _block(
        0, _ret(), _live(1), _array(NUMBERS, ()), _done(), _drop(1), _dead(1))))


def test_a_constant_array_with_one_part_for_each_element_passes():
    value = k.Aggregate(TRIPLE, (k.Int(2, I32), k.Int(3, I32), k.Int(5, I32)))
    _passes(_function([BLANK, TRIPLE], _block(
        0, _ret(), _set(1, r.Use(p.Const(value))), _done())))


def test_an_element_read_a_length_and_a_bounds_switch_pass():
    element = p.Index(_local(1), _copy(2))
    _passes(_function([BLANK, NUMBERS, I64, I32, U64, U64, BOOL, I32, I64], _block(
        0, tm.SwitchInt(_copy(6), ((0, BlockId(1)),), BlockId(2), SPAN),
        _set(3, r.Len(_local(1))), _set(8, r.Cast(_copy(3), I64)),
        _set(4, r.Cast(_copy(2), U64)), _set(5, r.Cast(_copy(8), U64)),
        _set(6, r.Binary("<", _copy(4), _copy(5)))),
        _block(1, tm.Trap("RE2020", (_copy(2), _copy(8)), SPAN)),
        _block(2, _ret(), _set(7, r.Use(p.Copy(element))), _done()),
        params=((1, GUARANTEED), (2, GUARANTEED))))


def test_a_byte_of_a_string_and_its_length_pass():
    _passes(_function([BLANK, STRING, BuiltinType.U8, I32], _block(
        0, _ret(), _set(2, r.Use(p.Copy(p.Index(_local(1), _i32(0))))),
        _set(3, r.Len(_local(1))), _done()), params=((1, GUARANTEED),)))


def test_a_view_that_a_slice_writes_drops_nothing_and_passes():
    _passes(_function([BLANK, NUMBERS, NUMBERS, I32], _block(
        0, _ret(), _live(2), _set(2, r.Slice(_local(1), _i64(0), _i64(1))),
        _set(3, r.Use(p.Copy(p.Index(_local(2), _i32(0))))), _done(), _dead(2)),
        params=((1, GUARANTEED),)))


# -- one function for each rule --------------------------------------------------------


@pytest.mark.parametrize("ty,runs,parts", [
    (TRIPLE, (r.RunOne(), r.RunOne()), (_i32(1), _i32(2))),
    (TRIPLE, (r.RunRepeat(),), (_i32(1), _copy(2))),
    (TRIPLE, (r.RunOne(),), (p.Const(k.Float(1.0, BuiltinType.F64)),)),
    (NUMBERS, (r.RunRepeat(),), (_i32(1), _i32(2))),
    (NUMBERS, (r.RunOne(),), (_i32(1), _i32(2))),
    (FLOATS, (r.RunSequence(1),), (_i32(0), _i32(3))),
    (I32, (), ()),
], ids=["short", "run-time-count-of-fixed", "element-type", "count-type", "part-count",
        "sequence-of-floats", "no-array"])
def test_an_array_value_that_its_runs_do_not_fit_is_refused(ty, runs, parts):
    _refused(_function([BLANK, ty, I32], _block(0, _ret(), _array(ty, runs, *parts, span=BAD),
                                                 _done()),
                       params=((2, GUARANTEED),)), ARRAY_VALUE)


def test_a_constant_array_with_a_part_of_another_type_is_refused():
    value = k.Aggregate(TRIPLE, (k.Int(2, I32), k.Bool(True), k.Int(5, I32)))
    _refused(_function([BLANK, TRIPLE], _block(
        0, _ret(), _set(1, r.Use(p.Const(value)), BAD), _done())), ARRAY_VALUE)


@pytest.mark.parametrize("value", [
    r.Len(_local(2)),
    r.Slice(_local(1), p.Const(k.Int(0, BuiltinType.I64)), _i32(1)),
    r.Slice(_local(3), _i32(0), _i32(1)),
], ids=["length-of-an-i32", "bound-type", "slice-of-a-string"])
def test_a_length_or_a_slice_of_a_value_that_is_no_array_is_refused(value):
    _refused(_function([BLANK, NUMBERS, I32, STRING], _block(
        0, _ret(), _set(2, value, BAD), _done()),
        params=((1, GUARANTEED), (3, GUARANTEED))), LENGTH_SLICE)


@pytest.mark.parametrize("ty", [BuiltinType.U64, U32], ids=["u64", "u32"])
def test_an_element_at_an_index_that_is_no_i64_is_refused(ty):
    element = p.Index(_local(1), _copy(2))
    _refused(_function([BLANK, NUMBERS, ty, I32], _block(
        0, _ret(), _set(3, r.Use(p.Copy(element)), BAD), _done()),
        params=((1, GUARANTEED), (2, GUARANTEED))), TYPED_PLACES)


def test_a_view_that_another_statement_writes_is_refused():
    _refused(_function([BLANK, NUMBERS, NUMBERS], _block(
        0, _ret(), _set(2, r.Slice(_local(1), _i64(0), _i64(1))),
        _set(2, r.Use(_copy(1)), BAD), _done()),
        params=((1, GUARANTEED),)), VIEW)


def test_a_move_of_a_view_is_refused():
    _refused(_function([BLANK, NUMBERS, NUMBERS, NUMBERS], _block(
        0, _ret(), _set(2, r.Slice(_local(1), _i64(0), _i64(1))),
        _set(3, r.Use(_move(2)), BAD), _drop(3), _done()),
        params=((1, GUARANTEED),)), VIEW)


def test_a_copy_of_a_view_into_an_owning_local_is_refused():
    _refused(_function([BLANK, NUMBERS, NUMBERS, NUMBERS], _block(
        0, _ret(), _set(2, r.Slice(_local(1), _i64(0), _i64(1))),
        _set(3, r.Use(_copy(2)), BAD), _drop(3), _done()),
        params=((1, GUARANTEED),)), VIEW)


def test_a_drop_of_a_view_is_refused():
    _refused(_function([BLANK, NUMBERS, NUMBERS], _block(
        0, _ret(), _set(2, r.Slice(_local(1), _i64(0), _i64(1))), st.Drop(_local(2), BAD),
        _done()), params=((1, GUARANTEED),)), VIEW)


def test_a_move_of_an_element_is_refused():
    element = p.Index(_local(1), _i32(0))
    _refused(_function([BLANK, NUMBERS, I32], _block(
        0, _ret(), _set(2, r.Use(p.Move(element)), BAD), _done()),
        params=((1, OWNED),)), ELEMENT_MOVE)
