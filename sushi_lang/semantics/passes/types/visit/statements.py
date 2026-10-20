"""The statement arms of the typecheck pass over SHIR (docs/design/ir.md 7.5).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`visit.ARMS`). The body walk starts here, and an arm that holds a block
calls it again. `check_params` gives each parameter its type before the walk: the written
type, resolved, with no `peek` or `poke` (the convention holds the mode, ir.md 4.1).

An `Assign` of a local is a `let` (`is_init`) or a rebind; the value is checked at the
type of the local. A `let` writes the type of its local, and a `let` that writes none is
CE2007, except for a hidden local, which takes the type of its value. An `Assign` of any
other place, and a reference `let`, are not typed yet. A `Return` of a bare body checks
its value at the declared return type, and refuses a `Result` constructor (CE2091); a
`Return` of a body with a channel, and of an initializer, are not typed yet.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.compatibility import (
    reject_mismatch, reject_return_mismatch)
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.utils import resolve_declared_type, validate_type_name
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.typesys import BuiltinType, ReferenceType

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_params(validator: TypeValidator, body: ShirBody) -> None:
    """The type of each parameter: its written type, resolved."""
    answers = validator.body_answers()
    for param in body.params:
        ty = validator.written_type(param.ty)
        answers.record_local(param.local, resolve_variable_type(validator, ty, param.loc))


def check_block(validator: TypeValidator, block: ShirBlock) -> None:
    """Check each statement of `block`, in the written order."""
    for statement in block.statements:
        visit.check(validator, statement)


def check_assign(validator: TypeValidator, node: s.Assign) -> None:
    if not isinstance(node.target, e.LocalRef):
        visit.stop_at(validator, node)
    if node.is_init:
        _check_let(validator, node, node.target)
        return
    ty = visit.check_expr(validator, node.target)
    got = visit.check_expr(validator, node.value, ty)
    if ty is not None:
        reject_mismatch(validator, got, ty, node.loc, None)


def _check_let(validator: TypeValidator, node: s.Assign, target: e.LocalRef) -> None:
    """`let T x = v`: the written type is the type of the local, and `v` is checked at it."""
    local = validator.body_locals[target.local]
    declared = None if local.declared is None else validator.written_type(local.declared)
    if isinstance(declared, ReferenceType) or isinstance(node.value, e.Borrow):
        visit.stop_at(validator, node)
    answers = validator.body_answers()
    if declared is None:
        if local.name is not None:
            er.emit(validator.reporter, er.ERR.CE2007, local.loc, name=local.name)
            return
        inferred = visit.check_expr(validator, node.value)
        if inferred is not None:
            answers.record_local(local.id, inferred)
            visit.check_expr(validator, target)
        return
    refused = validate_type_name(validator, declared, local.declared_loc)
    if declared == BuiltinType.BLANK:
        er.emit(validator.reporter, er.ERR.CE2032, local.declared_loc)
        return
    ty = resolve_variable_type(validator, declared, local.declared_loc)
    answers.record_local(local.id, ty)
    visit.check_expr(validator, target)
    got = visit.check_expr(validator, node.value, ty)
    if not refused:
        reject_mismatch(validator, got, ty, node.value.loc, local.declared_loc)


def check_expr_stmt(validator: TypeValidator, node: s.ExprStmt) -> None:
    visit.check_expr(validator, node.value)


def check_return(validator: TypeValidator, node: s.Return) -> None:
    """A `return` of a bare body: a `Result` constructor is CE2091, and a `~` return type
    takes any value."""
    body = validator.body
    if validator.channel_result is not None or (
            body is not None and isinstance(body.origin, Initializer)):
        visit.stop_at(validator, node)
    value = node.value
    if (isinstance(value, e.EnumInit) and value.variant in ("Ok", "Err")
            and validator.decl_name(value.decl) == "Result"):
        er.emit(validator.reporter, er.ERR.CE2091, value.loc,
                callable=validator.body_name or "this body")
        return
    declared = validator.body_return_type
    if declared is None or declared == BuiltinType.BLANK:
        visit.check_expr(validator, node.value)
        return
    expected = resolve_declared_type(validator, declared)
    got = visit.check_expr(validator, node.value, expected)
    if expected is not None:
        reject_return_mismatch(validator, got, expected, node.value.loc)
