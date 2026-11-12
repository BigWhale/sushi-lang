"""The borrow arms of a field place, a field write and a destructure (docs/design/ir.md 7.9).

A field read reads its base, and a borrow or a write of a field is counted on the root of
the place. A root that a later group checks (a function used as a value) stops the walk
with its own class. The tests run the arms over SHIR built by hand, with a stand-in for the
pass; they do not run the compiler.
"""
from __future__ import annotations


from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import arms, expressions, statements
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.ids import ShirId

SPAN = Span(3, 5, 3, 20)
ROOT = Span(3, 5, 3, 9)


def _field(base: e.ShirExpr, name: str = "x") -> e.Field:
    return e.Field(ShirId(5), SPAN, base, name, SPAN)


def test_the_table_checks_the_a5_constructs() -> None:
    assert arms.ARMS[e.Field] is expressions.check_field
    assert arms.ARMS[e.StructInit] is expressions.check_struct_init
    assert arms.ARMS[e.TupleInit] is expressions.check_tuple_init
    assert arms.ARMS[s.Destructure] is statements.check_destructure
    assert arms.ARMS[e.Take] is expressions.check_take


