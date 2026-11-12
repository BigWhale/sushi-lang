"""What a generic function call reads: its type arguments and its signature at them.

The arm of the call is `visit/generic_calls.py`.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from .. import TypeValidator


def constraints_hold_in_template(validator: 'TypeValidator', loc, generic_func,
                                  type_args) -> bool:
    """The callee's constraints at the solved type arguments, in a template check (#1070).

    `loc` is where the template names the callee: a call, or a function value.

    The check of the monomorphize stage, keyed as it keys an instance, so a call that
    stage judged is not judged twice. An argument that is a type parameter of the
    template satisfies a constraint only when a constraint of its own promises it.
    """
    from sushi_lang.semantics.generics.extension_targets import function_instance_key
    from ..utils import call_constraint_check
    return call_constraint_check(validator)(
        tuple(generic_func.type_params or ()), tuple(type_args),
        function_instance_key(generic_func.unit_name, generic_func.name, type_args), loc,
        validator.reporter.filename, getattr(generic_func, "filename", None), True)


def substituted_sig(validator: 'TypeValidator', generic_func, type_args, name: str):
    """The signature of a generic callee at these type arguments, with no instance.

    What a template check measures a call and a function value against (#1070): the
    substituted signature, its instances interned in the overlay and each parameter
    resolved. None when the count does not fit the declaration.
    """
    from sushi_lang.semantics.generics.monomorphize.transformer import substituted_param
    from sushi_lang.semantics.generics.types import substituted_call_signature
    from sushi_lang.semantics.passes.collect.functions import FuncSig
    from sushi_lang.semantics.type_resolution import resolve_type_recursively
    from ..utils import intern_signature

    signature = substituted_call_signature(generic_func, type_args)
    if signature is None:
        return None
    substituted, ret, err = signature
    intern_signature(validator, ret, err, *(p.ty for p in substituted))
    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    params = [p if p.ty is None
              else substituted_param(p, resolve_type_recursively(p.ty, structs, enums))
              for p in substituted]
    return FuncSig(name=name, params=params,
                   ret_type=resolve_type_recursively(ret, structs, enums), err_type=err,
                   borrowed_return=getattr(generic_func, "borrowed_return", None))


def reject_unsolved_generic_value(validator: 'TypeValidator', loc, name: str,
                                  expected_ty) -> None:
    """CE2093 for a generic function value that no stated function type solves.

    The one text for the bare name and the name behind an alias (#1105).
    """
    from sushi_lang.semantics.typesys import FunctionType
    if isinstance(expected_ty, FunctionType):
        reason = (f"the function type '{display_type(expected_ty)}' of this position "
                  "does not solve its type arguments")
        help_text = (f"state a function type whose parameter types solve each type "
                     f"parameter of '{name}'")
    else:
        reason = "this position states no function type, so its type arguments are unknown"
        help_text = (f"state the function type at the position, for example "
                     f"'let fn(i32) -> i32 g = {name}'")
    er.emit_with(validator.reporter, er.ERR.CE2093, loc, name=name,
                 reason=reason).help(help_text).emit()


def solved_value_type_args(validator: 'TypeValidator', generic_func, expected_ty) -> Optional[tuple]:
    """The type arguments of a generic function value that the function type of its position
    solves, or None. A pack parameter takes no value."""
    from sushi_lang.semantics.typesys import FunctionType
    from sushi_lang.semantics.generics.pack_inference import solve_leading_type_args
    if not isinstance(expected_ty, FunctionType):
        return None
    type_params = generic_func.type_params or []
    if type_params and type_params[-1].is_pack:
        return None
    return solve_leading_type_args(
        generic_func, list(expected_ty.param_types),
        validator.struct_table.by_name, validator.enum_table.by_name,
        ret_type=expected_ty.ok_type)

