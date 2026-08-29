"""Generic function call validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Dict

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import declared_modes
from sushi_lang.semantics.typesys import StructType, EnumType, UnknownType, Type
from sushi_lang.semantics.ast import Call
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.explicit_type_args import (
    resolve_explicit_type_args,
    check_explicit_type_arg_arity,
)
from ..visibility import name_is_contested, reject_private_call
from ..compatibility import types_compatible
from ..utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall

if TYPE_CHECKING:
    from .. import TypeValidator


def validate_generic_function_call(
    validator: 'TypeValidator',
    call: Call,
    function_name: str,
    generic_func=None,
) -> None:
    """Validate generic function call and rewrite to use mangled name.

    A qualified call (`geo.twin(...)`) resolved its declaration through the alias's
    provider already and hands it in; a bare call resolves through the unit ladder
    here (#495).
    """

    if generic_func is None:
        generic_func = validator.generic_sig(function_name)
    if generic_func is None:
        generic_func = validator.generic_func_table.by_name[function_name]

    # Visibility first: nothing below it is worth saying. A source library's units are
    # ordinary units here, so the call resolves and only the backend used to notice --
    # and it noticed as a KeyError (#467).
    if reject_private_call(validator, "function", generic_func, call.callee.loc):
        return

    explicit = call.type_args
    if explicit:
        # Explicit `@(...)` type args override inference (issue #137).
        expected = check_explicit_type_arg_arity(generic_func, len(explicit))
        if expected is not None:
            er.emit(
                validator.reporter,
                er.ERR.CE2062,
                call.type_args_loc or call.callee.loc,
                name=function_name,
                expected=expected,
                got=len(explicit),
            )
            return
        type_args = resolve_explicit_type_args(
            explicit, validator.struct_table, validator.enum_table
        )
    else:
        type_args = _infer_type_args_from_call_site(validator, call, generic_func)
        if type_args is None:
            if not name_is_contested(validator, "function", function_name):
                er.emit(
                    validator.reporter,
                    er.ERR.CE2060,
                    call.callee.loc,
                    name=function_name,
                    reason="could not infer type arguments from call site"
                )
            return

    # Per-element perk-constraint check for a constrained type-pack (CE2090).
    _validate_pack_element_constraints(validator, call, generic_func, type_args)

    # Generate mangled name. When the function's LAST type-param is a pack, the
    # symbol carries the pack arity so it matches the monomorphizer's ".pack{N}"
    # name (mirrors monomorphize/functions.py).
    type_params = generic_func.type_params or []
    has_pack = bool(type_params) and getattr(type_params[-1], "is_pack", False)
    if has_pack:
        pack_arity = len(type_args) - (len(type_params) - 1)
        mangled_name = mangle_function_name(
            function_name, type_args, pack_arity=pack_arity
        )
    else:
        mangled_name = mangle_function_name(function_name, type_args)

    if validator.func_sig(mangled_name) is None:
        er.emit(
            validator.reporter,
            er.ERR.CE2061,
            call.callee.loc,
            name=function_name,
            mangled=mangled_name,
            type_args=", ".join(display_type(t) for t in type_args)
        )
        return

    call.callee.id = mangled_name

    func_sig = validator.func_sig(mangled_name)

    validate_call_arguments(validator, call, func_sig)


def resolve_generic_fn_reference(validator: 'TypeValidator', name: str, expected_ty):
    """Resolve a bare generic-fn reference against an expected FunctionType (T2.3)."""
    from sushi_lang.semantics.typesys import FunctionType, UnknownType
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    if not isinstance(expected_ty, FunctionType):
        return None
    generic_func = validator.generic_sig(name)
    if generic_func is None:
        return None
    type_params = generic_func.type_params or []
    if type_params and getattr(type_params[-1], "is_pack", False):
        return None
    func_params = [p for p in generic_func.params if not getattr(p, "is_pack", False)]
    if len(func_params) != len(expected_ty.param_types):
        return None

    type_param_map: Dict[str, Type] = {}
    for param, exp_pty in zip(func_params, expected_ty.param_types, strict=False):
        if param.ty is None:
            return None
        if not _unify_types_for_inference(param.ty, exp_pty, type_param_map):
            return None
    if generic_func.ret is not None:
        if not _unify_types_for_inference(generic_func.ret, expected_ty.ok_type, type_param_map):
            return None

    type_args = []
    for tp in type_params:
        tp_name = tp.name if hasattr(tp, "name") else str(tp)
        if tp_name not in type_param_map:
            return None
        type_args.append(resolve_unknown_type(
            type_param_map[tp_name], validator.struct_table, validator.enum_table))
    type_args = tuple(type_args)

    mangled_name = mangle_function_name(name, type_args)
    func_sig = validator.func_sig(mangled_name)
    if func_sig is None:
        return None
    param_types = tuple(p.ty for p in func_sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = func_sig.ret_type
    err_type = func_sig.err_type if func_sig.err_type is not None else UnknownType("StdError")
    concrete_ft = FunctionType(param_types=param_types, ok_type=ok_type, err_type=err_type,
                               param_modes=declared_modes(func_sig.params))
    return mangled_name, concrete_ft


def _infer_type_args_from_call_site(
    validator: 'TypeValidator',
    call: Call,
    generic_func
) -> Optional[tuple]:
    """Infer type arguments from call site arguments."""
    from sushi_lang.semantics.generics.pack_inference import infer_flat_type_args
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    call_args = getattr(call, "args", []) or []
    arg_types = []
    for arg_expr in call_args:
        arg_type = validator.infer_expression_type(arg_expr)
        if arg_type is None or isinstance(arg_type, UnknownType):
            return None
        resolved = resolve_unknown_type(
            arg_type, validator.struct_table, validator.enum_table
        )
        arg_types.append(resolved)

    def _infer_leading(gfunc, leading_arg_types):
        """Existing Pass-2 leading unification, restricted to the fixed prefix."""
        type_param_map: Dict[str, Type] = {}

        leading_params = [
            p for p in gfunc.params if not getattr(p, "is_pack", False)
        ]
        if len(leading_arg_types) != len(leading_params):
            return None

        for arg_type, param in zip(leading_arg_types, leading_params, strict=False):
            if param.ty is None:
                return None
            if not _unify_types_for_inference(param.ty, arg_type, type_param_map):
                return None

        leading_tps = [
            tp for tp in gfunc.type_params if not getattr(tp, "is_pack", False)
        ]
        leading_args = []
        for tp in leading_tps:
            tp_name = tp.name if hasattr(tp, "name") else str(tp)
            if tp_name not in type_param_map:
                return None
            resolved = resolve_unknown_type(
                type_param_map[tp_name], validator.struct_table, validator.enum_table
            )
            leading_args.append(resolved)
        return tuple(leading_args)

    return infer_flat_type_args(
        generic_func, arg_types, infer_leading=_infer_leading
    )


def _validate_pack_element_constraints(
    validator: 'TypeValidator',
    call: Call,
    generic_func,
    flat_type_args: tuple
) -> None:
    """Per-element perk-constraint check for a constrained type-pack (CE2090)."""
    type_params = generic_func.type_params or []
    if not type_params:
        return

    pack_tp = type_params[-1]
    if not getattr(pack_tp, "is_pack", False):
        return

    constraints = getattr(pack_tp, "constraints", None) or []
    if not constraints:
        return

    leading_count = len(type_params) - 1
    pack_element_types = list(flat_type_args[leading_count:])

    for elem_index, elem_ty in enumerate(pack_element_types):
        type_name = _type_name_for_constraint(elem_ty)
        for perk_name in constraints:
            if not validator.perk_impl_table.implements(type_name, perk_name):
                er.emit(
                    validator.reporter,
                    er.ERR.CE2090,
                    call.callee.loc,
                    index=elem_index,
                    ty=display_type(elem_ty),
                    perk=perk_name,
                )


def _type_name_for_constraint(ty: Type) -> str:
    """Extract the lookup name used by the perk implementation table."""
    if isinstance(ty, (StructType, EnumType)):
        return ty.name
    return str(ty)


def _unify_types_for_inference(
    param_type: Type,
    arg_type: Type,
    type_param_map: Dict[str, Type]
) -> bool:
    """Unify parameter type with argument type for type inference (the typecheck pass)."""
    from sushi_lang.semantics.generics.unify import unify_types
    return unify_types(param_type, arg_type, type_param_map)


def validate_call_arguments(
    validator: 'TypeValidator',
    call: Call,
    func_sig
) -> None:
    """Validate call arguments against function signature."""
    expected_params = func_sig.params
    actual_args = call.args

    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=func_sig.name, expected=len(expected_params), got=len(actual_args))

    for i, (arg, param) in enumerate(zip(actual_args, expected_params, strict=False)):
        propagate_enum_type_to_dotcall(validator, arg, param.ty)

        propagate_struct_type_to_dotcall(validator, arg, param.ty)

        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(param.ty, StructType):
            struct_name = arg.callee.id
            if struct_name in validator.generic_struct_table.by_name:
                arg.callee.id = param.ty.name

        validator.validate_expression(arg)

        if param.ty is not None:  # Skip if parameter has unknown type
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, param.ty):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=display_type(param.ty), got=display_type(arg_type))

    for i in range(len(expected_params), len(actual_args)):
        validator.validate_expression(actual_args[i])
