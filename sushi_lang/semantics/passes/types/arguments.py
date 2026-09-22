"""The one argument check: the arity, then each argument against the parameter it fills.

Every call-shaped position in the typecheck pass measures its arguments the same way --
count them, propagate the parameter's declared type into each one, walk it, compare, and
say where it does not fit. The shape was written once per callee kind, and a copy that
falls behind is a behaviour the compiler has on one path and has lost on another: the
generics copy lost the bloom-spread refusal and the borrow help (#747), and the FFI copy
numbered its arguments from zero (#746).

What legitimately differs between the callers is a four-tuple -- the callee's display
name, the parameter types, the arity code and the mismatch code -- plus three policy
questions, each a keyword:

  ``minimum_arity``  a variadic callee's FIXED prefix. More arguments than parameters is
                     the tail, not a fault, and the tail carries its own rule at each
                     caller.
  ``stop_on_arity``  the callers that report the count and then say nothing more.
  ``walk_arguments`` an FFI call, whose arguments the namespaced caller walked already:
                     measure them, do not walk them twice.

The codes travel IN, so this module spells none of them. That is what lets the enum
constructor read the same check with CE2049/CE2050, which name a VARIANT where the
function pair names a function. The gate is
``tests/unit/test_argument_check_is_one.py``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Call, Expr, MemberAccess, Name, Spread
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import ReferenceType, StructType, Type

from .compatibility import types_compatible
from .propagation import propagate_declared_type_to_value

if TYPE_CHECKING:
    from . import TypeValidator


def check_arguments(
    validator: 'TypeValidator',
    callee_name: str,
    params: Sequence[Optional[Type]],
    args: Sequence[Expr],
    loc: Any,
    *,
    mismatch_code: er.ErrorMessage,
    arity_code: er.ErrorMessage,
    minimum_arity: bool = False,
    stop_on_arity: bool = False,
    walk_arguments: bool = True,
) -> bool:
    """Measure `args` against `params`, and answer whether the COUNT fit.

    `params` holds the expected types in order, one per parameter: a caller holding
    `Param` records hands in `[p.ty for p in params]`. `loc` is the span that names the
    callee, which is where a wrong count is reported; a wrong TYPE is reported at the
    argument itself.

    The answer is the arity alone, for the callers that stop there. A caller that wants
    every fault reported ignores it.
    """
    arity_ok = (len(args) >= len(params) if minimum_arity
                else len(args) == len(params))
    if not arity_ok:
        er.emit(validator.reporter, arity_code, loc,
                expected=len(params), got=len(args),
                **_callee_key(arity_code, callee_name))
        if stop_on_arity:
            return False

    for index, (arg, declared) in enumerate(zip(args, params, strict=False), start=1):
        if reject_misplaced_spread(validator, arg):
            continue
        if walk_arguments:
            expected = propagate_declared_type_to_value(validator, arg, declared)
            _retarget_generic_struct_constructor(validator, arg, expected)
            # `validate_expression` walks the argument AND answers its type. Asking the
            # inference a second time told the user twice about any fault an inference
            # arm reports -- `poke geo.SIZE` read its CE2400 twice (#685).
            actual = validator.validate_expression(arg)
        else:
            expected = declared
            actual = validator.infer_expression_type(arg)
        if expected is None or actual is None:
            continue
        if not types_compatible(validator, actual, expected):
            _emit_mismatch(validator, mismatch_code, callee_name, arg, index,
                           expected, actual)

    if minimum_arity:
        return arity_ok

    for extra in args[len(params):]:
        if reject_misplaced_spread(validator, extra):
            continue
        if walk_arguments:
            validator.validate_expression(extra)
    return arity_ok


def reject_misplaced_spread(validator: 'TypeValidator', arg: Expr) -> bool:
    """Refuse a bloom spread `arr...` in a position that is never variadic (CE0120).

    The inner expression is still validated, so nothing downstream reads an unwalked
    argument. Answers True when the argument was refused.
    """
    if not isinstance(arg, Spread):
        return False
    er.emit(validator.reporter, er.ERR.CE0120, arg.loc,
            message="bloom argument 'arr...' is only allowed as the last argument "
                    "of a call to a variadic '...T' function")
    validator.validate_expression(arg)
    return True


def _callee_key(code: er.ErrorMessage, callee_name: str) -> Dict[str, str]:
    """The placeholder a code's registry text spells for the callee.

    The function pair (CE2006/CE2009) names a `{name}`, the enum-constructor pair
    (CE2049/CE2050) a `{variant}`, and CE2006 names neither. Reading the registry text
    is what keeps the seam total over a code it has not met.
    """
    if "{variant}" in code.text:
        return {"variant": callee_name}
    if "{name}" in code.text:
        return {"name": callee_name}
    return {}


def _spell_place(arg: Expr) -> Optional[str]:
    """The source spelling of a PLACE the user can borrow, or None when there is none.

    The help offers the argument back to the user, so it must be text the user could type.
    A name spells itself and a member chain spells its receiver first; anything else --
    a call, an index, a literal -- has no spelling here and is offered no help.
    """
    if isinstance(arg, Name):
        return arg.id
    if isinstance(arg, MemberAccess):
        receiver = _spell_place(arg.receiver)
        return None if receiver is None else f"{receiver}.{arg.member}"
    return None


def _emit_mismatch(validator: 'TypeValidator', code: er.ErrorMessage, callee_name: str,
                   arg: Expr, index: int, expected_ty: Type, actual_ty: Type) -> None:
    """Report the mismatch, and say how to borrow when the parameter wants a borrow."""
    diag = er.emit_with(validator.reporter, code, getattr(arg, 'loc', None),
                        index=index, expected=display_type(expected_ty),
                        got=display_type(actual_ty),
                        **_callee_key(code, callee_name))
    place = _spell_place(arg) if isinstance(expected_ty, ReferenceType) else None
    if place is not None:
        diag.help(f"borrow it at the call site: `{expected_ty.mutability} {place}`")
    diag.emit()


def _retarget_generic_struct_constructor(validator: 'TypeValidator', arg: Expr,
                                         expected_ty: Optional[Type]) -> None:
    """A generic struct constructor written inline takes the INSTANCE the parameter names.

    `f(Box(1))` against a `Box@(i32)` parameter builds that instantiation; the
    constructor carries the template's name until a position says which one it is.
    """
    if not isinstance(arg, Call) or not isinstance(expected_ty, StructType):
        return
    callee = getattr(arg, "callee", None)
    if getattr(callee, "id", None) in validator.generic_struct_table.by_name:
        callee.id = expected_ty.name
