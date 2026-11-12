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
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
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


@pytest.mark.parametrize("convention", [Convention.GUARANTEED, Convention.IN_GUARANTEED,
                                        Convention.INOUT])
def test_a_move_of_a_parameter_that_is_no_owner_is_an_internal_error(convention):
    placement = DropPlacement()
    placement.parameter(_l(1), convention, True)
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


def _drop_if_set(index: int, flag: int) -> st.DropIfSet:
    return st.DropIfSet(p.Local(_l(index)), _l(flag), SPAN)


def _set(flag: int, value: bool) -> st.Assign:
    return st.Assign(p.Local(_l(flag)), r.Use(p.Const(k.Bool(value))), SPAN)


def test_a_join_of_two_states_makes_a_flagged_local_unknown_and_its_exit_reads_the_flag():
    placement = _placement((1, True))
    placement.flag(_l(1), _l(2))
    assert placement.flag_declared(_l(1), SPAN) == [st.StorageLive(_l(2), SPAN), _set(2, True)]
    at_branch = placement.state()
    placement.move(_l(1), SPAN)
    assert placement.flush() == [_set(2, False)]
    moved = placement.state()
    assert placement.join([at_branch, moved], placement.in_scope()) == ()
    assert placement.close_scope(SPAN) == [_drop_if_set(1, 2), _dead(2), _dead(1)]


def test_a_join_of_two_states_for_a_local_with_no_flag_names_the_local():
    placement = _placement((1, True), (2, True))
    at_branch = placement.state()
    placement.move(_l(2), SPAN)
    assert placement.join([at_branch, placement.state()], placement.in_scope()) == (_l(2),)


def test_a_join_of_one_state_keeps_it_exact():
    placement = _placement((1, True))
    placement.flag(_l(1), _l(2))
    placement.move(_l(1), SPAN)
    moved = placement.state()
    assert placement.join([moved, moved], placement.in_scope()) == ()
    assert placement.close_scope(SPAN) == [_dead(2), _dead(1)]


def test_a_rebind_of_an_unknown_local_reads_the_flag_and_sets_it():
    placement = _placement((1, True))
    placement.flag(_l(1), _l(2))
    placement.mark_unknown(_l(1))
    assert placement.holds_live_value(_l(1))
    assert placement.drop_old_value(_l(1), SPAN) == [_drop_if_set(1, 2)]
    assert placement.flag_stored(_l(1), SPAN) == [_set(2, True)]
    assert placement.close_scope(SPAN) == [_drop(1), _dead(2), _dead(1)]


def test_a_move_of_an_unknown_local_is_an_internal_error():
    placement = _placement((1, True))
    placement.flag(_l(1), _l(2))
    placement.mark_unknown(_l(1))
    with pytest.raises(InternalCompilerError):
        placement.move(_l(1), SPAN)


def test_a_rebind_after_a_move_cancels_the_flag_write_of_the_move():
    placement = _placement((1, True))
    placement.flag(_l(1), _l(2))
    placement.move(_l(1), SPAN)
    placement.drop_old_value(_l(1), SPAN)
    assert placement.flush() == []


def test_an_unknown_owned_parameter_reads_its_flag_at_the_function_exit():
    placement = DropPlacement()
    placement.parameter(_l(1), Convention.OWNED, True)
    placement.flag(_l(1), _l(2))
    placement.open_scope()
    placement.mark_unknown(_l(1))
    assert placement.exit_function(SPAN) == [_drop_if_set(1, 2), _dead(2)]


def test_a_jump_exits_the_scopes_after_a_depth_and_keeps_them_open():
    placement = _placement((1, True))
    depth = placement.depth()
    placement.open_scope()
    placement.declare(_l(2), True)
    placement.open_statement()
    assert placement.exit_to(depth, SPAN) == [_drop(2), _dead(2)]
    placement.leave_scope()
    assert placement.close_scope(SPAN) == [_drop(2), _dead(2)]


def test_the_locals_in_scope_are_the_owning_locals_and_the_owned_parameters():
    placement = DropPlacement()
    placement.parameter(_l(1), Convention.OWNED, True)
    placement.parameter(_l(2), Convention.INOUT, True)
    placement.open_scope()
    placement.declare(_l(3), True)
    placement.declare(_l(4), False)
    assert placement.in_scope() == (_l(1), _l(3))


def test_a_back_edge_that_brings_another_state_changes_the_header():
    placement = _placement((1, True), (2, True), (3, True))
    placement.flag(_l(3), _l(4))
    placement.move(_l(1), SPAN)
    placement.mark_unknown(_l(3))
    header = placement.state()
    placement.drop_old_value(_l(1), SPAN)
    placement.drop_old_value(_l(3), SPAN)
    assert header.changed_by([placement.state()], placement.in_scope()) == (_l(1),)
