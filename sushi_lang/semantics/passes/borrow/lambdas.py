"""The arm of a lambda (docs/design/ir.md 7.9, ruling Q8).

Each capture of `LiftResults` is a consuming use of the captured local, at the lambda: the
row of the capture is the one answer of its mode. The body is walked where the lambda stands,
as a callable of its own: it starts from no local of the enclosing body. In the body, each
capture has the state of a `poke` parameter, because the environment is the storage of the
closure and a second call reads it again: a write through it stays, and a consuming use of it
is the consuming use of a borrow (CE2411). A borrow of one capture is counted on that capture
alone. The rows of the body go into the table of the enclosing body, and a local of the body
has its row as any other local.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.body import ShirParam
from sushi_lang.semantics.shir.ids import LocalId
from sushi_lang.semantics.shir.typeck import lambda_conventions
from sushi_lang.semantics.shir.walk import walk
from sushi_lang.semantics.typesys import BorrowMode, ReferenceType

from .consume import consume_named, name_provenance
from .expressions import read_local
from .flow import record_exit, terminates
from .state import BorrowState
from .statements import check_block

if TYPE_CHECKING:
    from . import BorrowChecker


def check_lambda(checker: 'BorrowChecker', node: e.Lambda) -> None:
    environment = checker.shir.lift_table_of(checker.body).environment_of(node.shir_id) or ()
    for local in environment:
        _capture(checker, node, local)
    _walk_body(checker, node, environment)


def _capture(checker: 'BorrowChecker', node: e.Lambda, local: LocalId) -> None:
    """The consuming use of the capture, at the lambda. A capture of a capture of a lambda
    around this one is a read through its environment, at its first use in the body."""
    state = checker.borrow_state.get(local)
    span = _first_use(node, local) if state is not None and state.is_capture else node.loc
    read_local(checker, local, span)
    use = e.LocalRef(node.shir_id, node.loc, local)
    decision = consume_named(checker, use, name_provenance(checker, local), span, write=False)
    if not checker.discovery_rounds and not checker.typecheck_failed:
        checker.table().record_capture_ownership(node.shir_id, local, decision)


def _first_use(node: e.Lambda, local: LocalId) -> Span:
    for _path, value in walk(node.body, "body"):
        if isinstance(value, e.LocalRef) and value.local == local:
            return value.loc
    return node.loc


def _walk_body(checker: 'BorrowChecker', node: e.Lambda,
               environment: tuple[LocalId, ...]) -> None:
    saved = (checker.borrow_state, checker.active_borrows, checker._scope_binding_borrows,
             checker.block_locals, checker.loop_frames, checker.statement_at,
             checker.borrowed_return, checker.is_main)
    checker.borrow_state, checker.active_borrows = {}, set()
    checker._scope_binding_borrows, checker.block_locals = [], []
    checker.loop_frames, checker.statement_at = [], []
    checker.borrowed_return, checker.is_main = None, False
    try:
        for local in environment:
            _declare_capture(checker, node, local)
        conventions = lambda_conventions(checker.answers(), node)
        for param, convention in zip(node.params, conventions, strict=True):
            ty = checker.local_type(param.local)
            declared = ShirParam(param.local, ty, convention, node.loc, False)
            checker._declare_param(declared, has_receiver=False, located=True)
        checker.block_locals.append([param.local for param in node.params])
        check_block(checker, node.body)
        if not terminates(node.body):
            record_exit(checker)
    finally:
        (checker.borrow_state, checker.active_borrows, checker._scope_binding_borrows,
         checker.block_locals, checker.loop_frames, checker.statement_at,
         checker.borrowed_return, checker.is_main) = saved


def _declare_capture(checker: 'BorrowChecker', node: e.Lambda, local: LocalId) -> None:
    ty = checker.local_type(local)
    checker.declare(BorrowState(
        name=checker.name_of(local), local=local,
        var_type=None if ty is None else ReferenceType(ty, BorrowMode.POKE),
        declared_at_span=node.loc, is_borrow_param=True, is_capture=True))
