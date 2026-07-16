# semantics/passes/types/calls/generics.py
"""
Generic function call validation.

Handles validation for:
- Generic function calls with type inference
- Type parameter unification
- Mangled name rewriting
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Dict

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import StructType, EnumType, UnknownType, Type
from sushi_lang.semantics.ast import Call
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from ..compatibility import types_compatible
from ..utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall

if TYPE_CHECKING:
    from .. import TypeValidator


def validate_generic_function_call(
    validator: 'TypeValidator',
    call: Call,
    function_name: str
) -> None:
    """Validate generic function call and rewrite to use mangled name.

    Args:
        validator: Type validator instance
        call: Call AST node
        function_name: Generic function name
    """

    # Get generic function definition
    generic_func = validator.generic_func_table.by_name[function_name]

    # Infer type arguments from call site (pack-aware via the shared helper)
    type_args = _infer_type_args_from_call_site(validator, call, generic_func)

    if type_args is None:
        # Type inference failed
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

    # Check if monomorphized version exists
    if mangled_name not in validator.func_table.by_name:
        # Should not happen if monomorphization ran correctly
        er.emit(
            validator.reporter,
            er.ERR.CE2061,
            call.callee.loc,
            name=function_name,
            mangled=mangled_name,
            type_args=str(type_args)
        )
        return

    # REWRITE: Update call node to use mangled name
    call.callee.id = mangled_name

    # Get monomorphized function signature
    func_sig = validator.func_table.by_name[mangled_name]

    # Validate arguments against parameters (existing logic)
    validate_call_arguments(validator, call, func_sig)


def resolve_generic_fn_reference(validator: 'TypeValidator', name: str, expected_ty):
    """Resolve a bare generic-fn reference against an expected FunctionType (T2.3).

    `let fn(i32) -> i32 g = identity` with `fn identity<T>(T x) T`: solve the type args
    by unifying the generic signature `fn(T) -> T` against the expected type, mangle, and
    return `(mangled_name, concrete_FunctionType)` when the monomorphized instance exists.
    Returns None when not resolvable this way (no expected fn type, arity mismatch, a type
    param unsolved by the expected type, a pack function, or no monomorphized instance).
    """
    from sushi_lang.semantics.typesys import FunctionType, UnknownType
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    if not isinstance(expected_ty, FunctionType):
        return None
    generic_func = validator.generic_func_table.by_name.get(name)
    if generic_func is None:
        return None
    # v1 slice: plain (non-pack) generic functions only.
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
    func_sig = validator.func_table.by_name.get(mangled_name)
    if func_sig is None:
        return None
    param_types = tuple(p.ty for p in func_sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = func_sig.ret_type
    err_type = func_sig.err_type if func_sig.err_type is not None else UnknownType("StdError")
    concrete_ft = FunctionType(param_types=param_types, ok_type=ok_type, err_type=err_type)
    return mangled_name, concrete_ft


def _infer_type_args_from_call_site(
    validator: 'TypeValidator',
    call: Call,
    generic_func
) -> Optional[tuple]:
    """Infer type arguments from call site arguments.

    This is similar to InstantiationCollector but uses the full type checker.

    Args:
        validator: Type validator
        call: Call AST node
        generic_func: Generic function definition

    Returns:
        Tuple of concrete types or None if inference fails. For a function ending
        in a type pack the flat tuple is ``(<leading inferred type-args>,
        *<trailing pack element types>)`` -- the pack-tail handling is shared with
        Pass 1.5 via ``pack_inference.infer_flat_type_args`` so both sites agree
        on the symbol. A valid arity-0 pack returns ``()`` (SUCCESS, not failure).
    """
    from sushi_lang.semantics.generics.pack_inference import infer_flat_type_args
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    # Infer all positional argument types up front (the shared helper operates on
    # concrete types). Bail exactly as before on un-inferable args.
    call_args = getattr(call, "args", []) or []
    arg_types = []
    for arg_expr in call_args:
        arg_type = validator.infer_expression_type(arg_expr)
        if arg_type is None or isinstance(arg_type, UnknownType):
            return None
        # Resolve UnknownType to concrete StructType/EnumType where possible so
        # both leading-unification and the pack tail see resolved types.
        resolved = resolve_unknown_type(
            arg_type, validator.struct_table, validator.enum_table
        )
        arg_types.append(resolved)

    def _infer_leading(gfunc, leading_arg_types):
        """Existing Pass-2 leading unification, restricted to the fixed prefix.

        For a NON-pack function the helper passes ALL args here, reproducing the
        legacy behavior byte-for-byte.
        """
        type_param_map: Dict[str, Type] = {}

        # Leading value-params are those that are NOT the pack value-param.
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

        # Leading type-params are the non-pack ones, in declaration order.
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
    """Per-element perk-constraint check for a constrained type-pack (CE2090).

    When the function's trailing type-param is a perk-constrained pack
    (``...Ts: Perk``), each concrete element type bound to the pack must satisfy
    every constraint perk. Emits CE2090 for each violating element, with the
    0-based position WITHIN THE PACK as ``index``.

    Non-pack and unconstrained-pack functions are no-ops.
    """
    type_params = generic_func.type_params or []
    if not type_params:
        return

    pack_tp = type_params[-1]
    if not getattr(pack_tp, "is_pack", False):
        return

    constraints = getattr(pack_tp, "constraints", None) or []
    if not constraints:
        return

    # Trailing (pack) element types are the flat args after the leading 1:1
    # type-params; pack arity == that tail length (matches the monomorphizer).
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
                    ty=str(elem_ty),
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
    """Unify parameter type with argument type for type inference (Pass 2).

    Thin wrapper over the shared ``unify_types`` engine.
    """
    from sushi_lang.semantics.generics.unify import unify_types
    return unify_types(param_type, arg_type, type_param_map)


def validate_call_arguments(
    validator: 'TypeValidator',
    call: Call,
    func_sig
) -> None:
    """Validate call arguments against function signature.

    This is the existing argument validation logic, extracted for reuse.

    Args:
        validator: Type validator
        call: Call AST node
        func_sig: Function signature
    """
    expected_params = func_sig.params
    actual_args = call.args

    # Check argument count
    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=func_sig.name, expected=len(expected_params), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding parameter type
    for i, (arg, param) in enumerate(zip(actual_args, expected_params, strict=False)):
        # Propagate expected types to DotCall nodes for generic enums (before validation)
        # This allows Maybe.None(), Result.Ok(), etc. to work as function arguments
        propagate_enum_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to DotCall nodes for generic structs (before validation)
        # This allows Own.alloc(42) to work as function arguments
        propagate_struct_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to Call nodes for generic struct constructors
        # This allows Box(42) to work when parameter expects Box<i32>
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(param.ty, StructType):
            struct_name = arg.callee.id
            # Check if this is a generic struct constructor
            if struct_name in validator.generic_struct_table.by_name:
                # Update the Call node's callee id to use the concrete type name
                arg.callee.id = param.ty.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        if param.ty is not None:  # Skip if parameter has unknown type
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, param.ty):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=str(param.ty), got=str(arg_type))

    # Validate any excess arguments (if more args than params)
    for i in range(len(expected_params), len(actual_args)):
        validator.validate_expression(actual_args[i])
