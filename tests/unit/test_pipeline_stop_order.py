"""The stop of the new pipeline: one stop for a program, chosen in a fixed order.

From the switch to SHIR, a program that the semantic passes accept stops with the stop
code CE0155, which names a stage and a construct. A stop ends the walk of its body, and
the analysis goes on, so it can meet more than one. The pipeline reports ONE, after
`--dump-shir`, and only when the analysis reported no error. The order:

1. The first stop of the typecheck pass, at the first construct that it cannot type.
2. Else an error hole whose codes no error offered (`check_error_holes`).
3. Else `borrow, <body>`: the first body that the typecheck pass finished meets the
   stage after it, which is not built yet.
4. Else `borrow, the program`: no body was walked.

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


def test_a_typecheck_stop_comes_before_a_hole_and_a_finished_body() -> None:
    stops = Stops()
    stops.finished("main", _B, "main.sushi")
    stops.stopped(_typecheck("Call"))
    hole = StageStop("lowering, error hole", "IndexAccess with no CE2122", _B)
    assert stops.first(hole).params == {"stage": "typecheck", "construct": "Call"}


def test_a_hole_comes_before_a_finished_body() -> None:
    stops = Stops()
    stops.finished("main", _B, "main.sushi")
    hole = StageStop("lowering, error hole", "IndexAccess with no CE2122", _A)
    assert stops.first(hole) is hole


def test_the_first_finished_body_meets_the_borrow_stage() -> None:
    stops = Stops()
    stops.finished("helper", _A, "main.sushi")
    stops.finished("main", _B, "main.sushi")
    stop = stops.first(None)
    assert stop.params == {"stage": "borrow", "construct": "helper"}
    assert (stop.span, stop.filename) == (_A, "main.sushi")


def test_a_program_with_no_walked_body_stops_at_borrow() -> None:
    stop = Stops().first(None)
    assert stop.code == "CE0155"
    assert stop.params == {"stage": "borrow", "construct": "the program"}
    assert stop.span is None
