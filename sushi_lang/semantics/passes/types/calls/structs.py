"""Struct constructor validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple

from sushi_lang.internals import errors as er
from ..visibility import name_is_contested
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.semantics.ast import Call, Expr
from ..compatibility import types_compatible
from ..propagation import propagate_types_to_value
from ..utils import (
    intern_declared_wrapper, reject_spread_args, resolve_declared_type)

if TYPE_CHECKING:
    from .. import TypeValidator


def validate_struct_constructor(validator: 'TypeValidator', call: Call) -> None:
    """Validate struct constructor call - field count, field names, and types."""
    # A bloom spread `arr...` is not a struct-construction argument (CE0120).
    if reject_spread_args(validator, call.args):
        return

    struct_name = call.callee.id

    if struct_name not in validator.struct_table.by_name:
        er.emit(validator.reporter, er.ERR.CE2001, call.callee.loc, name=struct_name)
        for arg in call.args:
            validator.validate_expression(arg)
        return

    struct_type = validator.struct_table.by_name[struct_name]

    expected_fields = list(struct_type.fields)

    field_names = getattr(call, 'field_names', None)

    if field_names is not None:
        _validate_named_struct_constructor(validator, call, struct_type, expected_fields, field_names)
    else:
        _validate_positional_struct_constructor(validator, call, struct_type, expected_fields)


def _validate_named_struct_constructor(
    validator: 'TypeValidator',
    call: Call,
    struct_type: StructType,
    expected_fields: List[Tuple[str, Type]],
    field_names: List[str]
) -> None:
    """Validate named struct constructor and reorder arguments."""
    from ..field_matcher import validate_and_reorder_named_args

    actual_args = call.args

    reordered_args = validate_and_reorder_named_args(
        struct_type,
        actual_args,
        field_names,
        validator.reporter,
        call.callee.loc
    )

    if reordered_args is None:
        # The names are spent even when they do not fit: the field matcher has already
        # said what is wrong with them, and CE6104 must not report them a second time.
        call.field_names = None
        for arg in actual_args:
            validator.validate_expression(arg)
        return

    call.args = reordered_args

    call.field_names = None

    _check_field_arguments(validator, reordered_args, expected_fields,
                           code=er.ERR.CE2083, field_key="field")


def _validate_positional_struct_constructor(
    validator: 'TypeValidator',
    call: Call,
    struct_type: StructType,
    expected_fields: List[Tuple[str, Type]]
) -> None:
    """Validate positional struct constructor (existing logic)."""
    actual_args = call.args

    if (len(actual_args) != len(expected_fields)
            and not name_is_contested(validator, "struct", struct_type.name)):
        er.emit(validator.reporter, er.ERR.CE2027, call.callee.loc,
               name=display_type(struct_type), expected=len(expected_fields),
               got=len(actual_args))

    _check_field_arguments(validator, actual_args, expected_fields,
                           code=er.ERR.CE2028, field_key="field_name")

    for i in range(len(expected_fields), len(actual_args)):
        validator.validate_expression(actual_args[i])


def _check_field_arguments(
    validator: 'TypeValidator',
    args: List[Expr],
    expected_fields: List[Tuple[str, Type]],
    *,
    code: er.ErrorMessage,
    field_key: str,
) -> None:
    """Check each argument against the field it fills, and stamp it for the backend.

    The one walk both constructors take: a named construction puts its arguments in
    field order first, and from there the two are the same work. Only the CODE differs
    -- CE2083 for a named construction, CE2028 for a positional one -- and the two
    registry texts spell one sentence with two placeholder names, which is why the
    keyword travels beside the code.
    """
    for arg, (field_name, field_type) in zip(args, expected_fields, strict=False):
        resolved_field_type = _resolve_field_type(validator, field_type)

        propagate_types_to_value(validator, arg, resolved_field_type)

        if (isinstance(arg, Call) and hasattr(arg.callee, 'id')
                and isinstance(resolved_field_type, StructType)
                and arg.callee.id in validator.generic_struct_table.by_name):
            arg.callee.id = resolved_field_type.name

        validator.validate_expression(arg)

        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_field_type):
            er.emit(validator.reporter, code, arg.loc,
                    expected=display_type(resolved_field_type),
                    got=display_type(arg_type), **{field_key: field_name})


def _resolve_field_type(validator: 'TypeValidator', field_type: Type) -> Type:
    """The concrete type a field's DECLARED type names.

    `resolve_declared_type` answers for every kind but one: a written wrapper may name an
    enum no declaration has built yet, so it is INTERNED. The `resolve` pass resolves
    every field before this reads one, so the intern is a guard and not a hot path.
    """
    interned = intern_declared_wrapper(validator, field_type)
    if interned is not None:
        return interned

    resolved = resolve_declared_type(validator, field_type)
    return resolved if resolved is not None else field_type
