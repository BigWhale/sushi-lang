"""The arm of a lambda (docs/design/ir.md 7.3, 7.4, 7.9).

The body of a `Lambda` is a block of the enclosing body, so it is walked where the lambda
stands and its answers are rows of the same `TypeckResults`. A written parameter type wins,
and a bare parameter takes its type from the function type of the position. The lambda takes
its channel from its annotation or from that function type, never from its body. The answer
is the function type of the value. Its `captures` are not empty when the lambda captures, and
that is what makes the value own (`owns_resource`). CE2094 refuses a borrow capture and a
parameter of an owning type.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.channel import callable_text, channel_result
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import ParamMode
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.compatibility import reject_return_mismatch
from sushi_lang.semantics.passes.types.control_flow import reject_fall_off
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.signatures import validate_error_channel
from sushi_lang.semantics.passes.types.utils import resolve_declared_type, validate_type_name
from sushi_lang.semantics.passes.types.visit.statements import check_block
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.typesys import (
    BuiltinType, FunctionType, ReferenceType, Type, deref_type, owns_resource)

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

_MODES = {mode.convention: mode for mode in ParamMode}


def check_lambda(validator: TypeValidator, node: e.Lambda,
                 expected: Optional[Type]) -> Optional[Type]:
    position = expected if isinstance(expected, FunctionType) else None
    written = _parameters(validator, node, position)
    if written is None:
        return None
    types, modes = written
    _reject_borrow_captures(validator, node)
    _reject_owning_parameters(validator, node, types)
    declared = _resolved(validator, node.ret, node)
    ok = declared if declared is not None else (None if position is None else position.ok_type)
    err = _resolved(validator, node.err, node)
    if err is None and node.ret is None and position is not None:
        err = position.err_type
    with _in_lambda(validator, node, ok, err):
        if node.expr_body:
            ok = _check_expression_body(validator, node, ok, declared)
        else:
            check_block(validator, node.body)
            if not validator.in_template_copy:
                reject_fall_off(validator, node.body, ok, err is not None, node.loc)
    _record_captures(validator, node)
    return FunctionType(param_types=types, ok_type=ok, err_type=err, captures=node.captures,
                        param_modes=modes)


def _resolved(validator: TypeValidator, ty: Optional[Type], node: e.Lambda) -> Optional[Type]:
    if ty is None:
        return None
    written = validator.written_type(ty)
    validate_type_name(validator, written, None)
    return resolve_variable_type(validator, written, node.loc)


def _parameters(validator: TypeValidator, node: e.Lambda, position: Optional[FunctionType]
                ) -> Optional[tuple[tuple[Optional[Type], ...], tuple[ParamMode, ...]]]:
    """The type of each parameter in the function type, a borrow with its reference type, and
    each mode. The local of a borrow has the type of its referent. None after an error."""
    answers = validator.body_answers()
    types: list[Optional[Type]] = []
    failed = False
    for index, param in enumerate(node.params):
        local = validator.body_locals[param.local]
        ty: Optional[Type] = None
        if local.declared is not None:
            written = validator.written_type(local.declared)
            refused = validate_type_name(validator, written, local.declared_loc)
            if written == BuiltinType.BLANK:
                er.emit(validator.reporter, er.ERR.CE2032, local.declared_loc)
                refused = True
            failed = failed or refused
            ty = resolve_variable_type(validator, written, local.declared_loc)
            mode = _MODES[param.convention]
            if mode.by_pointer:
                ty = ReferenceType(ty, mode.borrow_mode)
        elif position is not None and index < len(position.param_types):
            ty = position.param_types[index]
        types.append(ty)
        if ty is None:
            validator.refused_locals.add(param.local)
        else:
            answers.record_local(param.local, deref_type(ty))
    if failed:
        return None
    return tuple(types), tuple(_MODES[param.convention] for param in node.params)


def _reject_borrow_captures(validator: TypeValidator, node: e.Lambda) -> None:
    """A borrow capture is deferred to Tier 2: a parameter by address of the body or of a
    lambda around this one, and a local of a reference type."""
    answers = validator.body_answers()
    body = validator.body
    params = () if body is None else body.params
    by_address = {param.local for param in params if param.convention.by_address}
    for local in node.captures:
        if (local in by_address or local in validator.lambda_borrows
                or isinstance(answers.local_type(local), ReferenceType)):
            er.emit(validator.reporter, er.ERR.CE2094, node.loc,
                    reason=f"cannot capture '{validator.body_locals[local].name}': it is a "
                           f"borrow (peek/poke capture is deferred to Tier 2)")


def _reject_owning_parameters(validator: TypeValidator, node: e.Lambda,
                              types: tuple[Optional[Type], ...]) -> None:
    """A parameter of an owning type has no deep copy on the indirect call. A `string` is
    legal. A type that owns for each type argument is refused at the template, and a type that
    owns only for some is refused at the instance that owns."""
    drops = validator.drop_type_names
    for param, ty in zip(node.params, types, strict=True):
        if ty != BuiltinType.STRING and owns_resource(
                ty, drops, resolve=lambda each: resolve_declared_type(validator, each),
                opaque_owns=False):
            name = validator.body_locals[param.local].name
            er.emit(validator.reporter, er.ERR.CE2094, node.loc,
                    reason=f"lambda parameter '{name}' has an owning type '{display_type(ty)}'; "
                           f"owning function-value parameters are deferred to Tier 2")


@contextmanager
def _in_lambda(validator: TypeValidator, node: e.Lambda, ok: Optional[Type],
               err: Optional[Type]) -> Iterator[None]:
    """The state of the body that the `Return` arm and the `??` check read, for the walk of
    the lambda. A `| E` written in a lambda has no span: the check judges its name only."""
    validate_error_channel(validator, ok, err, None)
    saved = (validator.body_name, validator.body_return_type, validator.channel_result,
             validator.body_conversion, validator.lambda_borrows)
    validator.body_name = callable_text("", "lambda")
    validator.body_return_type = ok
    validator.channel_result = (None if err is None else channel_result(
        validator.struct_table, validator.enum_table, ok, err))
    validator.body_conversion = None
    validator.lambda_borrows = validator.lambda_borrows | {
        param.local for param in node.params if param.convention.by_address}
    try:
        yield
    finally:
        (validator.body_name, validator.body_return_type, validator.channel_result,
         validator.body_conversion, validator.lambda_borrows) = saved


def _check_expression_body(validator: TypeValidator, node: e.Lambda, ok: Optional[Type],
                           declared: Optional[Type]) -> Optional[Type]:
    """The body `|x| value`. With no written return type, the value says what the lambda
    answers, and the type of the position answers when it cannot. A channel wraps the value in
    `Result.Ok` where SLIR lowering cuts the function, so the value is checked as it stands."""
    returned = node.body.statements[0]
    if not isinstance(returned, s.Return):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="the expression body of a lambda is no return")
    value = returned.value
    blank = ok == BuiltinType.BLANK
    got = visit.check_expr(validator, value, None if blank else ok)
    if declared is None:
        return ok if got is None else got
    if not blank and ok is not None:
        reject_return_mismatch(validator, got, ok, value.loc)
    return declared


def _record_captures(validator: TypeValidator, node: e.Lambda) -> None:
    answers = validator.body_answers()
    for local in node.captures:
        ty = answers.local_type(local)
        if ty is not None:
            answers.record_capture(node.shir_id, local, deref_type(ty))
