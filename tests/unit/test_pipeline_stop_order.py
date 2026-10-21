"""The stop of the new pipeline: one stop for a program, chosen in a fixed order.

From the switch to SHIR, a program that the semantic passes accept stops with the stop
code CE0155, which names a stage and a construct. A stop ends the walk of its body, and
the analysis goes on, so it can meet more than one. The pipeline reports ONE, after
`--dump-shir`, and only when the analysis reported no error. The order:

1. The first stop of the typecheck pass, at the first construct that it cannot type.
2. Else the first stop of the lift pass, at the first construct that it cannot lift.
3. Else the first stop of the borrow pass, at the first construct that it cannot check.
4. Else an error hole whose codes no error offered (`check_error_holes`).
5. Else the first stop of SLIR lowering, at the first construct that it cannot lower.
6. Else no stop: every body that reached the SLIR lowering finished, and the program goes
   on to the backend. A stop at `emit` comes from the emitter, not from this record.

These tests call the record over data. They do not run the compiler.
"""
from __future__ import annotations

from sushi_lang.internals.diagnostics import InternalCompilerError, StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir.stop import Stops

_A = Span(1, 1, 1, 4)
_B = Span(2, 1, 2, 4)


def _typecheck(construct: str, span: Span = _A) -> StageStop:
    return StageStop("typecheck", construct, span, "main.sushi")


def _lift(construct: str, span: Span = _A) -> StageStop:
    return StageStop("lift", construct, span, "main.sushi")


def _borrow(construct: str, span: Span = _A) -> StageStop:
    return StageStop("borrow", construct, span, "main.sushi")


def _slir(construct: str, span: Span = _A) -> StageStop:
    return StageStop("slir", construct, span, "main.sushi")


def test_a_stop_is_the_internal_error_ce0155() -> None:
    stop = StageStop("typecheck", "Return", _A, "main.sushi")
    assert isinstance(stop, InternalCompilerError)
    assert stop.code == "CE0155"
    assert stop.params == {"stage": "typecheck", "construct": "Return"}
    assert (stop.span, stop.filename) == (_A, "main.sushi")


def test_the_first_typecheck_stop_is_kept() -> None:
    stops = Stops()
    stops.stopped(_typecheck("Return"))
    stops.stopped(_typecheck("Assign", _B))
    assert stops.first(None).params["construct"] == "Return"


def test_a_typecheck_stop_comes_before_a_borrow_stop_and_a_hole() -> None:
    stops = Stops()
    stops.borrow_stopped(_borrow("If", _B))
    stops.stopped(_typecheck("Call"))
    hole = StageStop("lowering, error hole", "IndexAccess with no CE2122", _B)
    assert stops.first(hole).params == {"stage": "typecheck", "construct": "Call"}


def test_a_typecheck_stop_comes_before_a_lift_stop() -> None:
    stops = Stops()
    stops.lift_stopped(_lift("Lambda"))
    stops.stopped(_typecheck("Call", _B))
    assert stops.first(None).params == {"stage": "typecheck", "construct": "Call"}


def test_the_first_lift_stop_is_kept() -> None:
    stops = Stops()
    stops.lift_stopped(_lift("Lambda"))
    stops.lift_stopped(_lift("Lambda", _B))
    stop = stops.first(None)
    assert stop.params == {"stage": "lift", "construct": "Lambda"}
    assert stop.span == _A


def test_a_lift_stop_comes_before_a_borrow_stop_and_a_hole() -> None:
    stops = Stops()
    stops.borrow_stopped(_borrow("If", _B))
    stops.lift_stopped(_lift("Lambda"))
    hole = StageStop("lowering, error hole", "IndexAccess with no CE2122", _B)
    assert stops.first(hole).params == {"stage": "lift", "construct": "Lambda"}


def test_the_first_borrow_stop_is_kept() -> None:
    stops = Stops()
    stops.borrow_stopped(_borrow("If"))
    stops.borrow_stopped(_borrow("Loop", _B))
    assert stops.first(None).params == {"stage": "borrow", "construct": "If"}


def test_a_borrow_stop_comes_before_a_hole() -> None:
    stops = Stops()
    stops.borrow_stopped(_borrow("Match"))
    hole = StageStop("lowering, error hole", "IndexAccess with no CE2122", _B)
    assert stops.first(hole).params == {"stage": "borrow", "construct": "Match"}


def test_a_hole_comes_before_a_slir_stop() -> None:
    stops = Stops()
    stops.slir_stopped(_slir("Call", _B))
    hole = StageStop("lowering, error hole", "IndexAccess with no CE2122", _A)
    assert stops.first(hole) is hole


def test_the_first_slir_stop_is_kept() -> None:
    stops = Stops()
    stops.slir_stopped(_slir("ShirParam"))
    stops.slir_stopped(_slir("Call", _B))
    stop = stops.first(None)
    assert stop.params == {"stage": "slir", "construct": "ShirParam"}
    assert stop.span == _A


def test_a_program_with_no_stop_goes_on_to_the_backend() -> None:
    assert Stops().first(None) is None


def test_a_hole_alone_is_the_stop() -> None:
    hole = StageStop("lowering, error hole", "IndexAccess with no CE2122", _A)
    assert Stops().first(hole) is hole


def test_the_record_has_no_emit_stage() -> None:
    assert not hasattr(Stops(), "finished")
    assert not hasattr(Stops(), "next_stage")
