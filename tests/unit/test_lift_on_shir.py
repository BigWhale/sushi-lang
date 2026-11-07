"""The lift pass over SHIR, between typecheck and borrow (docs/design/ir.md 7.9, ruling Q8).

The typecheck pass gives the lift pass each body that it finished. The lift pass writes the
`LiftResults` of the body, checks the table when it ends the body (`check_lift`), and gives
the body to the next stage, the borrow pass. These tests give the pass the hand-made body of
`ownership_sample.py`, which holds a lambda, and a body with no lambda. They call a Python
function over data; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import CONVERSION_METHOD, ExtendDef, FuncDef
from sushi_lang.semantics.passes import lift as lift_module
from sushi_lang.semantics.passes.lift import LiftPass
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ExpandCopies, Instance, ShirBody
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.lowering import LoweredProgram
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.walk import walk

from ownership_sample import CAPTURED, sample, span

_TABLES = object()
_PERKLESS = SimpleNamespace(perk_impls=SimpleNamespace(implementations={}))
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


def _run(body: ShirBody, decls: DeclNames, *, decl: object = _DECL, tables: Any = _TABLES
         ) -> tuple[_NextStage, LoweredProgram]:
    next_stage = _NextStage()
    program = _program(body, decls)
    LiftPass(program, next_stage=next_stage, unit="u").check_body(
        decl, body, tables=tables, filename="main.sushi")
    return next_stage, program


def test_a_lambda_has_its_symbol_and_its_captured_locals() -> None:
    made = sample()
    lam = made.id_of("lambda")
    node = next(value for _path, value in walk(made.body.block, "block")
                if isinstance(value, e.Lambda))
    node.captures = (CAPTURED,)
    decl = FuncDef(name="sample", params=[], ret=None, body=None, loc=span(1))
    next_stage, program = _run(made.body, made.decls, decl=decl, tables=_PERKLESS)
    table = program.lift_table_of(made.body)
    assert table.symbol_of(lam) == "u$sample.__lambda_0"
    assert table.environment_of(lam) == (CAPTURED,)
    assert [call[1] for call in next_stage.calls] == [made.body]


def test_a_conversion_has_its_name_as_the_prefix() -> None:
    made = sample()
    decl = ExtendDef(target_type=None, name=CONVERSION_METHOD, params=[], ret=None, body=None,
                     loc=span(1))
    next_stage, program = _run(made.body, made.decls, decl=decl, tables=_PERKLESS)
    assert program.lift_table_of(made.body).symbol_of(made.id_of("lambda")) == "sample.__lambda_0"
    assert [call[1] for call in next_stage.calls] == [made.body]


def test_a_body_with_no_lambda_goes_to_borrow_with_its_tables() -> None:
    body, decls = _body_with_no_lambda()
    next_stage, _program_ = _run(body, decls)
    assert next_stage.calls == [(_DECL, body, _TABLES, "main.sushi")]


def test_the_lift_pass_checks_its_table_when_it_ends_a_body(
        monkeypatch: pytest.MonkeyPatch) -> None:
    body, decls = _body_with_no_lambda()
    checked: list[ShirBody] = []
    monkeypatch.setattr(lift_module, "check_lift",
                        lambda checked_body, table, names: checked.append(checked_body))
    _run(body, decls)
    assert checked == [body]
