"""SLIR drop placement over hand-built locals (docs/design/ir.md 8.10).

`DropPlacement` is the one home of the drops that SHIR-to-SLIR lowering writes: the exit
of a scope (rules 1 and 2), a moved local (rule 3), a parameter (rule 5) and the old value
of a rebind (rule 6). These tests give it locals and moves by hand and read the statements
that it places. They call a Python function over data; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.drops import DropPlacement
from sushi_lang.semantics.slir.ids import LocalId

SPAN = Span(3, 5, 3, 9)


def _l(index: int) -> LocalId:
    return LocalId(index)


def _drop(index: int) -> st.Drop:
    return st.Drop(p.Local(_l(index)), SPAN)


def _dead(index: int) -> st.StorageDead:
    return st.StorageDead(_l(index), SPAN)


def _placement(*locals_: tuple[int, bool]) -> DropPlacement:
    placement = DropPlacement()
    placement.open_scope()
    for index, owning in locals_:
        placement.declare(_l(index), owning)
    return placement


def test_a_scope_exit_drops_the_newest_declaration_first():
    placement = _placement((1, True), (2, False), (3, True))
    assert placement.close_scope(SPAN) == [_drop(3), _dead(3), _dead(2), _drop(1), _dead(1)]


def test_a_moved_local_gets_no_drop():
    placement = _placement((1, True), (2, True))
    placement.move(_l(2), SPAN)
    assert placement.close_scope(SPAN) == [_dead(2), _drop(1), _dead(1)]


def test_a_second_move_of_a_moved_local_is_an_internal_error():
    placement = _placement((1, True))
    placement.move(_l(1), SPAN)
    with pytest.raises(InternalCompilerError):
        placement.move(_l(1), SPAN)


def test_the_function_exit_drops_each_scope_and_then_each_owned_parameter():
    placement = DropPlacement()
    placement.parameter(_l(1), Convention.OWNED, True)
    placement.parameter(_l(2), Convention.GUARANTEED, True)
    placement.parameter(_l(3), Convention.INOUT, True)
    placement.parameter(_l(4), Convention.OWNED, False)
    placement.open_scope()
    placement.declare(_l(5), True)
    placement.open_scope()
    placement.declare(_l(6), False)
    assert placement.exit_function(SPAN) == [_dead(6), _drop(5), _dead(5), _drop(1)]


def test_an_owned_parameter_that_is_moved_gets_no_drop():
    placement = DropPlacement()
    placement.parameter(_l(1), Convention.OWNED, True)
    placement.open_scope()
    placement.move(_l(1), SPAN)
    assert placement.exit_function(SPAN) == []


def test_a_move_of_a_borrowed_parameter_is_an_internal_error():
    placement = DropPlacement()
    placement.parameter(_l(1), Convention.IN_GUARANTEED, True)
    with pytest.raises(InternalCompilerError):
        placement.move(_l(1), SPAN)


def test_a_rebind_drops_a_live_old_value_and_the_new_value_is_live():
    placement = _placement((1, True))
    assert placement.holds_live_value(_l(1))
    assert placement.drop_old_value(_l(1), SPAN) == [_drop(1)]
    assert placement.close_scope(SPAN) == [_drop(1), _dead(1)]


def test_a_rebind_of_a_moved_local_drops_nothing_and_makes_it_live():
    placement = _placement((1, True))
    placement.move(_l(1), SPAN)
    assert not placement.holds_live_value(_l(1))
    assert placement.drop_old_value(_l(1), SPAN) == []
    assert placement.close_scope(SPAN) == [_drop(1), _dead(1)]


def test_a_rebind_through_an_inout_parameter_drops_the_old_value():
    placement = DropPlacement()
    placement.parameter(_l(1), Convention.INOUT, True)
    placement.open_scope()
    assert placement.drop_old_value(_l(1), SPAN) == [_drop(1)]
    assert placement.exit_function(SPAN) == []


def test_a_scope_that_a_path_left_already_places_nothing_when_it_goes():
    placement = _placement((1, True))
    placement.open_scope()
    placement.declare(_l(2), True)
    placement.leave_scope()
    assert placement.close_scope(SPAN) == [_drop(1), _dead(1)]


def test_a_temporary_drops_at_the_end_of_its_statement_and_a_let_local_at_its_block():
    placement = _placement((1, True))
    placement.open_statement()
    placement.temporary(_l(2), True)
    placement.declare(_l(3), True)
    assert placement.close_scope(SPAN) == [_drop(2), _dead(2)]
    assert placement.close_scope(SPAN) == [_drop(3), _dead(3), _drop(1), _dead(1)]


def test_a_return_exits_the_temporaries_of_its_statement_first():
    placement = DropPlacement()
    placement.parameter(_l(1), Convention.OWNED, True)
    placement.open_scope()
    placement.declare(_l(2), True)
    placement.open_statement()
    placement.temporary(_l(3), False)
    assert placement.exit_function(SPAN) == [_dead(3), _drop(2), _dead(2), _drop(1)]
