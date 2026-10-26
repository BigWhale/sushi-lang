"""The statement arms of the typecheck pass over SHIR (docs/design/ir.md 7.5).

`check_block` checks the statements of one block in the written order, each through the
table of the pass (`visit.ARMS`). The body walk starts here, and an arm that holds a block
calls it again. `check_params` gives each parameter its type before the walk: the written
type, resolved, with no `peek` or `poke` (the convention holds the mode, ir.md 4.1).

An `Assign` of a local is a `let` (`is_init`) or a rebind; the value is checked at the
type of the local. A `let` writes the type of its local, and a `let` that writes none is
CE2007, except for a hidden local, which takes the type of its value. A `let` whose value
does not fit keeps its written type, and the pass records the local as refused: a method
call on it reports nothing more. An `Assign` of any
other place, and a reference `let`, are not typed yet. An expression statement drops its
value, and a dropped `Result` of a value is CW2001. A `Return` of a body with a channel
spells `Result.Ok(...)` or `Result.Err(...)` (CE2030), checked at the `Result` of the
channel (CE2031, CE2039). A `Return` of a bare body checks its value at the declared
return type, and refuses a `Result` constructor (CE2091). A `Return` of an initializer
is not typed yet.

An `If` and a `Loop` with a `Cond` source check their condition (`check_condition`) and
their blocks; an `elif` is an `If` in the `else` block. The kind of a `while` loop is
`While` (ir.md 7.7). A loop over items is not typed yet. A `break` and a `continue` have
no answer: the scope pass refuses one outside a loop.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.compatibility import (
    reject_mismatch, reject_return_mismatch, reject_unhandled_result)
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.result_validation import (
    spells_a_result, validate_result_pattern)
from sushi_lang.semantics.passes.types.statements import reject_unspelled_result
from sushi_lang.semantics.passes.types.utils import resolve_declared_type, validate_type_name
from sushi_lang.semantics.passes.types.visit.operators import check_condition
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody
from sushi_lang.semantics.shir.loop_kind import While
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import BuiltinType, EnumType, ReferenceType, Type

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
    if isinstance(declared, ReferenceType):
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
    if refused:
        return
    if (reject_unhandled_result(validator, got, ty, node.value.loc)
            or reject_mismatch(validator, got, ty, node.value.loc, local.declared_loc)):
        validator.refused_locals.add(local.id)


def check_expr_stmt(validator: TypeValidator, node: s.ExprStmt) -> None:
    """A value that the statement drops. A `Result` of a value loses its error: CW2001."""
    ty = visit.check_expr(validator, node.value)
    if not (isinstance(ty, EnumType) and is_instance_of(ty, "Result")):
        return
    ok = ty.get_variant("Ok")
    if ok is not None and ok.associated_types and ok.associated_types[0] == BuiltinType.BLANK:
        return
    er.emit(validator.reporter, er.ERR.CW2001, node.value.loc, ty=display_type(ty))


def check_return(validator: TypeValidator, node: s.Return) -> None:
    """A `return`. A body with a channel spells a `Result` constructor, and a bare body
    returns the value: a `Result` constructor is CE2091 there, and a `~` return type takes
    any value."""
    body = validator.body
    if body is not None and isinstance(body.origin, Initializer):
        visit.stop_at(validator, node)
    value = node.value
    channel = validator.channel_result
    if channel is not None:
        _check_channel_return(validator, value, channel)
        return
    if spells_a_result(validator, value):
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


def _check_channel_return(validator: TypeValidator, value: e.ShirExpr, channel: Type) -> None:
    """A `return` of a body with a channel spells `Result.Ok(...)` or `Result.Err(...)`,
    which is checked at the `Result` of the channel. Any other value is CE2030, because
    nothing wraps a bare value (#848); a copy of a template took that rule on the
    template."""
    if isinstance(value, e.EnumInit) and spells_a_result(validator, value):
        visit.check_expr(validator, value, channel)
        validate_result_pattern(validator, value, channel)
        return
    visit.check_expr(validator, value)
    if not validator.in_template_copy:
        reject_unspelled_result(validator, value)


def check_if(validator: TypeValidator, node: s.If) -> None:
    check_condition(validator, node.cond)
    check_block(validator, node.then)
    if node.otherwise is not None:
        check_block(validator, node.otherwise)


def check_loop(validator: TypeValidator, node: s.Loop) -> None:
    """A `while` loop: its condition, its kind and its body."""
    if not isinstance(node.source, s.Cond):
        visit.stop_at(validator, node)
    check_condition(validator, node.source.cond)
    validator.body_answers().record_loop_kind(node.shir_id, While())
    check_block(validator, node.body)


def check_jump(_validator: TypeValidator, _node: s.Break | s.Continue) -> None:
    """A `break` or a `continue`: no type and no answer."""
