"""The borrow arms of a field place, a field write and a destructure (docs/design/ir.md 7.9).

A field read reads its base, and a borrow or a write of a field is counted on the root of
the place. A root that a later group checks (a function used as a value) stops the walk
with its own class. A destructure of a value that owns stops,
and so does a marked take of an owning field. The tests run the arms over SHIR built by
hand, with a stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.passes.borrow import arms, borrows, expressions, statements
from sushi_lang.semantics.passes.borrow.state import BorrowState
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.typesys import BorrowMode

SPAN = Span(3, 5, 3, 20)
ROOT = Span(3, 5, 3, 9)


def _field(base: e.ShirExpr, name: str = "x") -> e.Field:
    return e.Field(ShirId(5), SPAN, base, name, SPAN)


def _roots() -> list:
    decls = DeclNames()
    return [e.GlobalRef(ShirId(15), ROOT, decls.refer("make"))]


def test_the_table_checks_the_a5_constructs() -> None:
    assert arms.ARMS[e.Field] is expressions.check_field
    assert arms.ARMS[e.StructInit] is expressions.check_struct_init
    assert arms.ARMS[e.TupleInit] is expressions.check_tuple_init
    assert arms.ARMS[s.Destructure] is statements.check_destructure


@pytest.mark.parametrize("root", _roots(), ids=["fn value"])
def test_a_borrow_of_a_field_of_a_later_root_stops_at_the_root(root: e.ShirExpr) -> None:
    """The stop comes before the arm reads the pass, so the stand-in is never read."""
    with pytest.raises(StageStop) as caught:
        borrows.check_borrow(object(), _field(_field(root, "a")),  # type: ignore[arg-type]
                             BorrowMode.POKE, SPAN)
    assert caught.value.params == {"stage": "borrow", "construct": type(root).__name__}
    assert caught.value.span == ROOT


@pytest.mark.parametrize("root", _roots(), ids=["fn value"])
def test_a_write_of_a_field_of_a_later_root_stops_at_the_root(root: e.ShirExpr) -> None:
    node = s.Assign(ShirId(1), SPAN, _field(root), e.Lit(ShirId(2), SPAN, e.IntConst(1, 10)),
                    False)
    with pytest.raises(StageStop) as caught:
        statements.check_assign(object(), node)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "borrow", "construct": type(root).__name__}


class _Types:
    """The stand-in of the type queries: every value owns."""

    def type_class(self, _ty: object) -> TypeClass:
        return TypeClass.MOVE


class _Checker:
    """The stand-in of the pass: no state, and a type for each local."""
    types = _Types()

    def __init__(self) -> None:
        self.borrow_state: dict = {}

    def local_type(self, _local: LocalId) -> object:
        return "(string, i32)"

    def declare(self, state: BorrowState) -> None:
        self.borrow_state[state.local] = state


def test_a_destructure_of_a_value_that_owns_stops_with_borrow_destructure() -> None:
    """The value is read first, and the stop comes before a row and before a binder: each
    binder of an owning element owns or borrows it, and a later group checks that."""
    value = e.LocalRef(ShirId(2), SPAN, LocalId(0))
    node = s.Destructure(ShirId(1), SPAN, [LocalId(1), s.Discard()], value)
    with pytest.raises(StageStop) as caught:
        statements.check_destructure(_Checker(), node)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "borrow", "construct": "Destructure"}
    assert caught.value.span == SPAN


class _Answers:
    """The stand-in of the answers of typecheck: every node has an owning type."""

    def type_of(self, _shir_id: ShirId) -> object:
        return "File"


class _TakeChecker(_Checker):
    """The stand-in of a body that owns the local `s`."""

    def __init__(self) -> None:
        super().__init__()
        self.declare(BorrowState(name="s", local=LocalId(0), var_type="Sink"))

    def answers(self) -> _Answers:
        return _Answers()


def test_a_nom_argument_of_an_owning_field_stops_with_borrow_field_take() -> None:
    """A marked field take off a local that the body owns spends the local, and group B7
    checks it. The stop comes before the argument is read."""
    take = _field(e.LocalRef(ShirId(3), SPAN, LocalId(0)), "out")
    node = e.EnumInit(ShirId(1), SPAN, DeclNames().refer("Maybe"), "Some",
                      [e.Arg(take, Convention.OWNED)], SPAN)
    with pytest.raises(StageStop) as caught:
        expressions.check_enum_init(_TakeChecker(), node)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "borrow", "construct": "Field take"}
    assert caught.value.span == SPAN
