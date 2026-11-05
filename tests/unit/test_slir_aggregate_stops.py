"""SLIR lowering stops at the construction of a struct with a field of a reference type (docs/design/ir.md 8.9).

CE2415 refuses the declaration, and the body of a construction can still reach SLIR. The
test calls the arm of `lower_aggregate.py` over data, before the arm evaluates a part; it
does not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType, ReferenceType, StructType, Type

SPAN = Span(4, 5, 4, 30)
STRING, I32 = BuiltinType.STRING, BuiltinType.I32
NAMED = StructType(name="Named", fields=(("name", STRING), ("n", I32)))


def _low(answers: TypeckResults, owning: tuple[Type, ...] = (STRING, NAMED)) -> Any:
    """The parts of a `Lowering` that an arm reads before it evaluates a part."""
    return SimpleNamespace(typeck=answers, owns=lambda ty: ty in owning,
                           type_of=lambda node: answers.type_of(node.shir_id))


def _stops(arm: Callable[..., object], construct: str, *args: object) -> None:
    with pytest.raises(StageStop) as caught:
        arm(*args)
    assert caught.value.params == {"stage": "slir", "construct": construct}
    assert caught.value.span == SPAN


def _typed(answers: TypeckResults, node: e.ShirExpr, ty: Type) -> e.ShirExpr:
    answers.record_type(node.shir_id, ty)
    return node


def test_a_struct_with_a_field_of_a_reference_type_stops() -> None:
    """CE2415 refuses the declaration; the body of a construction can still reach SLIR."""
    answers = TypeckResults()
    value = _typed(answers, e.LocalRef(ShirId(2), SPAN, LocalId(1)), I32)
    node = e.StructInit(ShirId(1), SPAN, DeclNames().refer("Holder"), [e.Arg(value, None)],
                        (0,), None, False, SPAN)
    answers.record_instance(node.shir_id, StructType(
        name="Holder", fields=(("value", ReferenceType(I32, BorrowMode.PEEK)),)))
    _stops(agg.struct_init, "StructInit reference field", _low(answers), node)
