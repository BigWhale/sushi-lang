"""The completeness check of `OwnershipResults`, and the rule of one answer per key (docs/design/ir.md 7.9).

The `borrow` pass writes every ownership answer of a body into its table (ruling S6). When
the pass ends on a body with no error, the check walks the body and finds each answer that
a node needs and that the table does not hold: the `Ownership` of each consuming use and
of each capture, LENT or OWNED for each iterator producer and lazy adapter, the heap
decision of each lent one, and the walk in place of each loop over an iterator value or a
`next()` type. A gap is the internal error CE0157. These tests give the check a body and
tables that are made by hand (`ownership_sample.py`). They call a Python function over
data; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.lift import LiftResults
from sushi_lang.semantics.shir.ownership import OwnershipResults
from sushi_lang.semantics.shir.ownership_check import (
    check_ownership, consuming_uses, missing_ownership,
)
from sushi_lang.semantics.shir.side_table import MissingAnswer

from ownership_sample import CONSUMED, OWNERSHIP_ROWS, Sample, lift, ownership, sample, span


def _missing(made: Sample, table: OwnershipResults,
             lifted: LiftResults | None = None) -> list[MissingAnswer]:
    return missing_ownership(made.body, made.typeck, lift(made) if lifted is None else lifted,
                             table)


def test_a_full_table_has_no_gap() -> None:
    made = sample()
    assert _missing(made, ownership(made)) == []
    check_ownership(made.body, made.typeck, lift(made), ownership(made), made.decls)


def test_the_consuming_uses_are_the_positions_of_the_rule() -> None:
    """Each consuming position, and no other: not a borrowed argument, not a repeated
    value, not a statement value, not the iterable of a direct walk or of a walk in place.
    The operand of every cast is one, a numeric cast too: a plain operand gets ADOPT."""
    made = sample()
    found = [node.shir_id for node in consuming_uses(made.body, made.typeck, ownership(made))]
    assert sorted(found) == sorted(made.id_of(name) for name in CONSUMED)


#: The row that a table leaves out -> the gap that the check finds.
GAPS: dict[str, MissingAnswer] = {
    "capture": MissingAnswer("ownership of capture _4", "Lambda", span(15)),
    "lent producer": MissingAnswer("iterator state", "Call", span(12)),
    "lent map_call": MissingAnswer("iterator state", "Call", span(12)),
    "heap producer": MissingAnswer("heap decision", "Call", span(12)),
    "heap map_call": MissingAnswer("heap decision", "Call", span(12)),
    "in place value_loop": MissingAnswer("walk in place", "Loop", span(12)),
    "in place protocol_loop": MissingAnswer("walk in place", "Loop", span(14)),
}


def test_each_row_of_the_sample_has_a_gap_case() -> None:
    assert set(GAPS) == set(OWNERSHIP_ROWS)


@pytest.mark.parametrize("row", sorted(GAPS))
def test_a_missing_row_is_found(row: str) -> None:
    made = sample()
    assert _missing(made, ownership(made, skip=row)) == [GAPS[row]]


@pytest.mark.parametrize("name", sorted(CONSUMED))
def test_a_consuming_use_with_no_ownership_is_found(name: str) -> None:
    made = sample()
    node = made.nodes[name]
    assert _missing(made, ownership(made, skip=name)) == [
        MissingAnswer("ownership", type(node).__name__, span(CONSUMED[name]))]


# -- a REJECT row with no error (docs/design/ir.md 7.9) ---------------------------------
# REJECT is an error of `borrow`, except at two positions, where it is a borrowed view:
# the value of a `let` (a let-borrow) and the operand of a `??` that reads through a
# borrowed owning wrapper. Anywhere else in a body that `borrow` finished, it is CE0157.

_NO_REJECT = "ownership other than reject"


def test_a_reject_at_the_value_of_a_let_is_a_borrowed_view() -> None:
    made = sample()
    assert _missing(made, ownership(made, reject="s_ref")) == []


@pytest.mark.parametrize("name", ["keep_call", "t_arg", "fail_call", "ok"],
                         ids=["rebind", "owned_argument", "cast_operand", "return"])
def test_a_reject_anywhere_else_is_found(name: str) -> None:
    made = sample()
    node = made.nodes[name]
    assert _missing(made, ownership(made, reject=name)) == [
        MissingAnswer(_NO_REJECT, type(node).__name__, span(CONSUMED[name]))]


def _try_body(*, returns: bool):
    """`let string got = r??` with `r` a borrowed owning `Result`; or `return r??`."""
    from sushi_lang.semantics.shir import exprs as e
    from sushi_lang.semantics.shir import stmts as s
    from sushi_lang.semantics.shir.body import (
        ExpandCopies, Instance, ShirBody, ShirLocal, ShirParam)
    from sushi_lang.semantics.shir.decls import DeclNames
    from sushi_lang.semantics.shir.nodes import ShirBlock
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.shir.typeck import TypeckResults
    from sushi_lang.semantics.typesys import BuiltinType

    at = span(1)
    inner = e.LocalRef(ShirId(0), at, LocalId(0))
    tried = e.Try(ShirId(1), at, inner)
    statement = (s.Return(ShirId(2), at, tried) if returns
                 else s.Assign(ShirId(2), at, e.LocalRef(ShirId(3), at, LocalId(1)), tried,
                               True))
    decls = DeclNames()
    body = ShirBody(decls.declare("read"), Instance(),
                    [ShirParam(LocalId(0), BuiltinType.STRING, Convention.GUARANTEED, at,
                               False)],
                    [ShirLocal(LocalId(0), "r", None, at), ShirLocal(LocalId(1), "got", None, at)],
                    ShirBlock(ShirId(4), at, [statement]), ExpandCopies())
    return body, TypeckResults(), inner, tried, decls


def test_a_reject_at_the_operand_of_a_try_is_a_borrowed_view() -> None:
    body, typeck, inner, tried, _decls = _try_body(returns=False)
    table = OwnershipResults()
    table.record_ownership(inner.shir_id, Ownership.REJECT)
    table.record_ownership(tried.shir_id, Ownership.REJECT)
    assert missing_ownership(body, typeck, LiftResults(), table) == []


def test_a_reject_at_the_return_of_a_try_is_found() -> None:
    body, typeck, inner, tried, decls = _try_body(returns=True)
    table = OwnershipResults()
    table.record_ownership(inner.shir_id, Ownership.REJECT)
    table.record_ownership(tried.shir_id, Ownership.REJECT)
    assert missing_ownership(body, typeck, LiftResults(), table) == [
        MissingAnswer(_NO_REJECT, "Try", span(1))]
    with pytest.raises(InternalCompilerError) as caught:
        check_ownership(body, typeck, LiftResults(), table, decls)
    assert caught.value.code == "CE0157"
    assert caught.value.params["answer"] == _NO_REJECT


# The operand of a `??` is a borrowed view only when it reads through a BORROWED wrapper:
# a local that a parameter with no `nom` binds, a let-borrow, or a reference. A REJECT at
# the operand of a `??` over an owned local or over a new value is a gap.


def _try_over(operand: str):
    """`let got = <operand>??` where the operand is the parameter `r`, owned (`nom r`) or
    borrowed, the local `v` bound from `r` (a let-borrow when its `let` answers REJECT),
    or the call `make()`. The answers: each row that the borrow pass writes."""
    from sushi_lang.semantics.shir import exprs as e
    from sushi_lang.semantics.shir import stmts as s
    from sushi_lang.semantics.shir.body import (
        ExpandCopies, Instance, ShirBody, ShirLocal, ShirParam)
    from sushi_lang.semantics.shir.decls import DeclNames
    from sushi_lang.semantics.shir.nodes import ShirBlock
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.shir.typeck import TypeckResults
    from sushi_lang.semantics.typesys import BuiltinType

    at = span(1)
    decls = DeclNames()
    table = OwnershipResults()
    statements = []
    convention = Convention.OWNED if operand == "owned_param" else Convention.GUARANTEED
    if operand in ("let_borrow", "owned_local"):
        bound = e.LocalRef(ShirId(10), at, LocalId(0))
        statements.append(s.Assign(ShirId(11), at, e.LocalRef(ShirId(12), at, LocalId(2)),
                                   bound, True))
        table.record_ownership(bound.shir_id, Ownership.REJECT if operand == "let_borrow"
                               else Ownership.MOVE)
        inner = e.LocalRef(ShirId(0), at, LocalId(2))
    elif operand == "call":
        callee = e.GlobalRef(ShirId(13), at, decls.refer("make"))
        inner = e.Call(ShirId(0), at, e.Named(callee), [])
    else:
        inner = e.LocalRef(ShirId(0), at, LocalId(0))
    tried = e.Try(ShirId(1), at, inner)
    statements.append(s.Assign(ShirId(2), at, e.LocalRef(ShirId(3), at, LocalId(1)), tried,
                               True))
    table.record_ownership(inner.shir_id, Ownership.REJECT)
    table.record_ownership(tried.shir_id, Ownership.ADOPT)
    body = ShirBody(decls.declare("read"), Instance(),
                    [ShirParam(LocalId(0), BuiltinType.STRING, convention, at, False)],
                    [ShirLocal(LocalId(0), "r", None, at), ShirLocal(LocalId(1), "got", None, at),
                     ShirLocal(LocalId(2), "v", None, at)],
                    ShirBlock(ShirId(4), at, statements), ExpandCopies())
    return body, TypeckResults(), table, inner


@pytest.mark.parametrize("operand", ["borrowed_param", "let_borrow"])
def test_a_reject_at_a_try_over_a_borrowed_wrapper_is_a_borrowed_view(operand: str) -> None:
    body, typeck, table, _inner = _try_over(operand)
    assert missing_ownership(body, typeck, LiftResults(), table) == []


@pytest.mark.parametrize("operand", ["owned_param", "owned_local", "call"])
def test_a_reject_at_a_try_over_a_wrapper_that_is_not_borrowed_is_found(operand: str) -> None:
    body, typeck, table, inner = _try_over(operand)
    assert missing_ownership(body, typeck, LiftResults(), table) == [
        MissingAnswer(_NO_REJECT, type(inner).__name__, span(1))]


def test_an_owned_iterator_needs_no_heap_decision() -> None:
    made = sample()
    table = OwnershipResults()
    table.record_lent(made.id_of("producer"), False)
    gaps = _missing(made, table)
    assert MissingAnswer("iterator state", "Call", span(12)) in gaps
    assert gaps.count(MissingAnswer("heap decision", "Call", span(12))) == 0


def test_a_lambda_with_no_environment_has_no_capture_gap() -> None:
    """The environment comes from `lift`; with no environment the check has no capture to
    count, and the completeness check of `LiftResults` finds that gap."""
    made = sample()
    gaps = _missing(made, ownership(made, skip="capture"), lift(made, skip="environment"))
    assert gaps == []


def test_an_empty_table_misses_every_row() -> None:
    made = sample()
    gaps = _missing(made, OwnershipResults())
    # The two lent answers are missing, so the check does not ask for a heap decision. The
    # walk in place of the value loop is missing, so the check does not ask for the
    # ownership of its iterable.
    assert len(gaps) == len(CONSUMED) + len(GAPS) - 3


def test_a_gap_is_the_internal_error_ce0157() -> None:
    made = sample()
    with pytest.raises(InternalCompilerError) as caught:
        check_ownership(made.body, made.typeck, lift(made),
                        ownership(made, skip="in place protocol_loop"), made.decls)
    error = caught.value
    assert error.code == "CE0157"
    assert error.params == {"writer": "borrow", "table": "OwnershipResults",
                            "answer": "walk in place", "node": "Loop",
                            "where": "line 14, column 5", "body": "sample"}
    assert error.span == span(14)


def test_the_first_gap_is_the_one_that_the_error_names() -> None:
    made = sample()
    table = OwnershipResults()
    with pytest.raises(InternalCompilerError) as caught:
        check_ownership(made.body, made.typeck, lift(made), table, made.decls)
    assert caught.value.params["where"] == "line 2, column 5"


# -- one answer per key ----------------------------------------------------------------


@pytest.mark.parametrize("write", [
    lambda table: table.record_ownership(ShirId(0), Ownership.MOVE),
    lambda table: table.record_capture_ownership(ShirId(0), LocalId(1), Ownership.ADOPT),
    lambda table: table.record_conditional_move(LocalId(0)),
    lambda table: table.record_lent(ShirId(0), True),
    lambda table: table.record_lent_on_heap(ShirId(0), False),
    lambda table: table.record_in_place(ShirId(0), True),
], ids=["ownership", "capture", "conditional move", "lent", "heap", "in place"])
def test_a_second_answer_for_one_key_is_an_internal_error(write) -> None:
    table = OwnershipResults()
    write(table)
    with pytest.raises(InternalCompilerError) as caught:
        write(table)
    assert caught.value.code == "CE0000"


def test_a_read_of_a_key_with_no_answer_is_none() -> None:
    table = OwnershipResults()
    assert table.ownership_of(ShirId(3)) is None
    assert table.capture_ownership_of(ShirId(3), LocalId(0)) is None
    assert table.lent_of(ShirId(3)) is None
    assert table.lent_on_heap(ShirId(3)) is None
    assert table.in_place_of(ShirId(3)) is None
    assert table.is_conditionally_moved(LocalId(3)) is False
    assert table.conditionally_moved() == ()


def test_the_conditionally_moved_locals_read_in_id_order() -> None:
    table = OwnershipResults()
    table.record_conditional_move(LocalId(7))
    table.record_conditional_move(LocalId(2))
    assert table.conditionally_moved() == (LocalId(2), LocalId(7))
    assert table.is_conditionally_moved(LocalId(7)) is True
    assert table.is_conditionally_moved(LocalId(3)) is False
