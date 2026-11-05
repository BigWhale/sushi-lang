"""The borrow arms of a field place, a field write and a destructure (docs/design/ir.md 7.9).

A field read reads its base, and a borrow or a write of a field is counted on the root of
the place. A root that a later group checks (a function used as a value) stops the walk
with its own class. The tests run the arms over SHIR built by hand, with a stand-in for the
pass; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import arms, borrows, expressions, statements
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId
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
    assert arms.ARMS[e.Take] is expressions.check_take


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
