"""The lift pass over SHIR, between typecheck and borrow (docs/design/ir.md 7.9, ruling Q8).

The typecheck pass gives the lift pass each body that it finished with no error. The lift
pass writes the `LiftResults` of the body, checks the table when it ends the body
(`check_lift`), and gives the body to the next stage, the borrow pass. A `Lambda` stops
the walk of its body with the stop code CE0155 (`lift, Lambda`) until the closures group:
the pass records the stop, writes no row, and does not give the body to borrow.

The typecheck pass stops at a `Lambda` first today, so no compile reaches the stop of the
lift pass. These tests give the pass the hand-made body of `ownership_sample.py`, which
holds a lambda, and a body with no lambda. They call a Python function over data; they do
not run the compiler.
"""
from __future__ import annotations

from typing import Any

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes import lift as lift_module
from sushi_lang.semantics.passes.lift import LiftPass
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ExpandCopies, Instance, ShirBody
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.lowering import LoweredProgram
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.stop import Stops

from ownership_sample import sample, span

_TABLES = object()
_DECL = object()


class _NextStage:
    """A stand-in for the borrow pass: it records each body that it gets."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ShirBody, Any, Any]] = []

    def __call__(self, decl: object, body: ShirBody, *, tables: Any, filename: Any) -> None:
        self.calls.append((decl, body, tables, filename))


def _program(body: ShirBody, decls: DeclNames) -> LoweredProgram:
    program = LoweredProgram(decls=decls)
    program.add(object(), _DECL, body, copy=False)  # type: ignore[arg-type]
    return program


def _body_with_no_lambda() -> tuple[ShirBody, DeclNames]:
    decls = DeclNames()
    loc = Span(1, 1, 1, 9)
    ret = s.Return(ShirId(1), loc, e.Lit(ShirId(0), loc, e.IntConst(0)))
    block = ShirBlock(ShirId(2), loc, [ret])
    return ShirBody(decls.declare("main"), Instance(), [], [], block, ExpandCopies()), decls


def _run(body: ShirBody, decls: DeclNames) -> tuple[Stops, _NextStage, LoweredProgram]:
    stops, next_stage = Stops(), _NextStage()
    program = _program(body, decls)
    LiftPass(program, stops=stops, next_stage=next_stage).check_body(
        _DECL, body, tables=_TABLES, filename="main.sushi")
    return stops, next_stage, program


def test_a_lambda_is_a_stop_of_the_lift_pass() -> None:
    made = sample()
    stops, _next, _program_ = _run(made.body, made.decls)
    stop = stops.first(None)
    assert isinstance(stop, StageStop)
    assert stop.params == {"stage": "lift", "construct": "Lambda"}
    assert (stop.span, stop.filename) == (span(15), "main.sushi")


def test_a_body_that_lift_stopped_does_not_reach_borrow() -> None:
    made = sample()
    stops, next_stage, program = _run(made.body, made.decls)
    assert next_stage.calls == []
    assert stops.next_stage is None
    lam = made.id_of("lambda")
    table = program.lift_table_of(made.body)
    assert (table.symbol_of(lam), table.environment_of(lam)) == (None, None)


def test_a_body_with_no_lambda_goes_to_borrow_with_its_tables() -> None:
    body, decls = _body_with_no_lambda()
    stops, next_stage, _program_ = _run(body, decls)
    assert next_stage.calls == [(_DECL, body, _TABLES, "main.sushi")]
    assert (stops.typecheck, stops.lift, stops.borrow) == (None, None, None)


def test_the_lift_pass_checks_its_table_when_it_ends_a_body(
        monkeypatch: pytest.MonkeyPatch) -> None:
    body, decls = _body_with_no_lambda()
    checked: list[ShirBody] = []
    monkeypatch.setattr(lift_module, "check_lift",
                        lambda checked_body, table, names: checked.append(checked_body))
    _run(body, decls)
    assert checked == [body]


def test_the_lift_pass_does_not_check_a_body_that_it_stopped(
        monkeypatch: pytest.MonkeyPatch) -> None:
    made = sample()
    checked: list[ShirBody] = []
    monkeypatch.setattr(lift_module, "check_lift",
                        lambda checked_body, table, names: checked.append(checked_body))
    _run(made.body, made.decls)
    assert checked == []
