"""The SLIR verifier over hand-built closure and iterator values (docs/design/ir.md 8.7, 8.13).

The parts of an `AggClosure` and of each row of an `AggIterator`. Each test gives the
per-function check one function; it calls a Python function over data and does not run the
compiler.
"""
from __future__ import annotations

from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir.verify_rules import CAPTURE_PLACES, ITERATOR_VALUE
from sushi_lang.semantics.typesys import (
    DynamicArrayType, FunctionType, IteratorType, StructType,
)

from tests.unit.test_slir_verifier import (
    BAD, BLANK, I32, _block, _copy, _drop, _function, _move, _passes, _refused, _ret, _set,
)

GUARANTEED = Convention.GUARANTEED
FN = FunctionType((I32,), I32, None, captures=())
ENV = StructType(name="__closure_env<i32>", fields=(("n", I32),))
ITERATOR = IteratorType(I32)
NUMBERS = DynamicArrayType(base_type=I32)
FN_REF = p.Const(k.FnRef("unit$lambda"))


def _closure(*parts: p.Operand, env: StructType | None = ENV) -> object:
    return _function([BLANK, FN, I32], _block(
        0, _ret(), _set(1, r.Aggregate(r.AggClosure(FN, env), parts), BAD),
        _set(0, r.Use(p.Const(k.Blank())))), params=((2, GUARANTEED),))


def _iterator(source: r.IterSource, *parts: p.Operand) -> object:
    return _function([BLANK, ITERATOR, NUMBERS, ITERATOR], _block(
        0, _ret(), _set(1, r.Aggregate(r.AggIterator(ITERATOR, source, True), parts), BAD),
        _drop(1), _set(0, r.Use(p.Const(k.Blank())))), params=((2, GUARANTEED), (3, GUARANTEED)))


def test_a_closure_with_its_fn_ref_and_one_copy_for_each_capture_passes():
    _passes(_closure(FN_REF, _copy(2)))


def test_a_closure_whose_first_part_is_no_fn_ref_is_refused():
    _refused(_closure(_copy(2), _copy(2)), CAPTURE_PLACES)


def test_a_closure_with_a_part_count_that_is_not_its_environment_is_refused():
    _refused(_closure(FN_REF), CAPTURE_PLACES)


def test_a_capture_that_is_a_constant_is_refused():
    _refused(_closure(FN_REF, p.Const(k.Int(1, I32))), CAPTURE_PLACES)


def test_a_lent_cursor_that_reads_its_container_passes():
    _passes(_iterator(r.IterElements(NUMBERS, True), _copy(2)))


def test_a_lent_cursor_that_moves_its_container_is_refused():
    _refused(_iterator(r.IterElements(NUMBERS, True), _move(2)), ITERATOR_VALUE)


def test_an_iterator_with_a_part_count_that_is_not_its_row_is_refused():
    _refused(_iterator(r.IterElements(NUMBERS, True), _copy(2), _copy(2)), ITERATOR_VALUE)


def test_an_adapter_that_copies_its_inner_iterator_is_refused():
    _refused(_iterator(r.IterEnumerate(ITERATOR), _copy(3)), ITERATOR_VALUE)
