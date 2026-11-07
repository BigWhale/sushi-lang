"""What a generic function call reads: its type arguments and its signature at them.

The arm of the call is `visit/generic_calls.py`.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import declared_modes
from sushi_lang.semantics.typesys import UnknownType
from sushi_lang.semantics.ast import Call
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.explicit_type_args import (
    resolve_explicit_type_args,
    check_explicit_type_arg_arity,
)

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
    from sushi_lang.semantics.generics.types import substituted_call_signature
    from sushi_lang.semantics.passes.collect.functions import FuncSig
    from sushi_lang.semantics.type_resolution import resolve_type_recursively
    from ..utils import intern_signature

    signature = substituted_call_signature(generic_func, type_args)
    if signature is None:
        return None
    params, ret, err = signature
    intern_signature(validator, ret, err, *(p.ty for p in params))
    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    for param in params:
        if param.ty is not None:
            param.ty = resolve_type_recursively(param.ty, structs, enums)
    return FuncSig(name=name, params=params,
                   ret_type=resolve_type_recursively(ret, structs, enums), err_type=err,
                   borrowed_return=getattr(generic_func, "borrowed_return", None))


def call_type_args(validator: 'TypeValidator', call: Call, generic_func) -> Optional[tuple]:
    """The type arguments a generic call site names -- explicit, or inferred. Silent.

    ONE reader, because the validating half and the inferring half must agree: the symbol
    the call is rewritten to and the type its result is stamped with are both read off
    these arguments. A wrong explicit arity answers None here and CE2062 there.
    """
    if call.type_args and check_explicit_type_arg_arity(
            generic_func, len(call.type_args)) is not None:
        return None
    return _named_or_inferred_type_args(validator, call, generic_func)


def _named_or_inferred_type_args(validator: 'TypeValidator', call: Call, generic_func,
                                 why: Optional[list[str]] = None) -> Optional[tuple]:
    """The explicit type arguments resolved, or the inferred ones. The arity is checked.

    `why` collects the reason an inference failed, for CE2060.
    """
    if call.type_args:
        return resolve_explicit_type_args(
            call.type_args, validator.struct_table.by_name, validator.enum_table.by_name)
    return _infer_type_args_from_call_site(validator, call, generic_func, why)


def generic_call_result_type(validator: 'TypeValidator', call: Call, generic_func):
    """What a call to a generic function yields, before its instance exists (#556).

    A call is normally typed through the callee's concrete signature, and a generic
    callee has none until the monomorphize pass builds one under a mangled symbol. The
    inferrer runs before that symbol is chosen -- a generic call sitting in another
    generic call's ARGUMENT is typed while the outer call solves its own parameters --
    and answering nothing there left the argument untyped, so the outer call was CE2060
    however the value was spelled. The substituted signature answers instead, the same
    derivation the instantiate pass reads for a `match` scrutinee (#549).
    """
    from sushi_lang.semantics.generics.types import substituted_call_result

    type_args = call_type_args(validator, call, generic_func)
    if type_args is None:
        return None
    return substituted_call_result(generic_func, type_args)


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


# What `resolve_generic_fn_reference` answers when a constraint refused the solved type
# arguments and said so (#1070): the caller adds no CE2093 on top.
GENERIC_VALUE_REFUSED = object()


def resolve_generic_fn_reference(validator: 'TypeValidator', name: str, expected_ty,
                                 generic_func=None, loc=None):
    """Resolve a generic-fn reference against an expected FunctionType (T2.3).

    A bare name reads the unit's own view; a name behind an alias hands in the
    declaration its provider resolved (#1017).
    """
    from sushi_lang.semantics.typesys import FunctionType
    from sushi_lang.semantics.generics.pack_inference import solve_leading_type_args
    if not isinstance(expected_ty, FunctionType):
        return None
    if generic_func is None:
        generic_func = validator.generic_sig(name)
    if generic_func is None:
        return None
    type_params = generic_func.type_params or []
    if type_params and type_params[-1].is_pack:
        return None
    type_args = solve_leading_type_args(
        generic_func, list(expected_ty.param_types),
        validator.struct_table.by_name, validator.enum_table.by_name,
        ret_type=expected_ty.ok_type)
    if type_args is None:
        return None

    if validator.in_template_check:
        # A value passes the template's type parameter on, as a call does: the callee's
        # constraints are checked where the value is written. The inferring half gives
        # no `loc` and asks nothing; the validating half reports.
        if loc is not None and not constraints_hold_in_template(
                validator, loc, generic_func, type_args):
            return GENERIC_VALUE_REFUSED
        func_sig = substituted_sig(validator, generic_func, type_args, name)
        mangled_name = name
    else:
        mangled_name = mangle_function_name(name, type_args)
        func_sig = validator.func_table.lookup(
            mangled_name, getattr(generic_func, "unit_name", None))
    if func_sig is None:
        return None
    param_types = tuple(p.ty for p in func_sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = func_sig.ret_type
    concrete_ft = FunctionType(param_types=param_types, ok_type=ok_type,
                               err_type=func_sig.err_type,
                               param_modes=declared_modes(func_sig.params))
    return mangled_name, concrete_ft


def _infer_type_args_from_call_site(
    validator: 'TypeValidator',
    call: Call,
    generic_func,
    why: Optional[list[str]] = None,
) -> Optional[tuple]:
    """Infer type arguments from call site arguments.

    A generic function value is not typed here: the callee types it, once the other
    arguments solve the callee (#1029). An argument that holds a range solves nothing:
    the range is the fault (CE2122), and the walk of the unchecked arguments says so.
    """
    from sushi_lang.semantics.generics.pack_inference import infer_flat_type_args
    from sushi_lang.semantics.ranges import holds_a_range_value
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    call_args = getattr(call, "args", []) or []
    arg_types: list = []
    for arg_expr in call_args:
        if names_generic_fn_value(validator, arg_expr):
            arg_types.append(None)
            continue
        if holds_a_range_value(arg_expr):
            return None
        arg_type = validator.infer_expression_type(arg_expr)
        if arg_type is None or isinstance(arg_type, UnknownType):
            return None
        arg_types.append(resolve_unknown_type(arg_type, structs, enums))

    return infer_flat_type_args(generic_func, arg_types, structs, enums, why)


def names_generic_fn_value(validator: 'TypeValidator', expr) -> bool:
    """The expression is a generic function written as a value, bare or behind an alias.

    A local and the unit's own concrete function win the bare name, as they do in
    `visit_name`.
    """
    from sushi_lang.semantics.ast import MemberAccess, Name
    from .user_defined import own_concrete_function
    if isinstance(expr, Name):
        return (expr.id not in validator.variable_types
                and own_concrete_function(validator.func_table, expr.id,
                                          validator.current_unit_name) is None
                and validator.generic_sig(expr.id) is not None)
    if isinstance(expr, MemberAccess) and isinstance(expr.receiver, Name):
        if expr.receiver.id in validator.variable_types:
            return False
        binding = validator.resolve_namespaced(expr.receiver, expr.member)
        return binding is not None and binding.kind == "generic function"
    return False
