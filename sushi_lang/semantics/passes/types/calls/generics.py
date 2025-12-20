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
    from sushi_lang.semantics.generics.types import TypeParameter

    # Get generic function definition
    generic_func = validator.generic_func_table.by_name[function_name]

    # Infer type arguments from call site
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

    # Generate mangled name
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
        Tuple of concrete types or None if inference fails
    """
    import sys

    # Build type parameter -> concrete type mapping
    type_param_map: Dict[str, Type] = {}

    # Get call arguments
    call_args = getattr(call, "args", []) or []
    func_params = generic_func.params

    # Check argument count
    if len(call_args) != len(func_params):
        return None

    # Match each argument to corresponding parameter
    for i, (arg_expr, param) in enumerate(zip(call_args, func_params)):
        # Infer argument type using full type checker
        arg_type = validator.infer_expression_type(arg_expr)

        if arg_type is None or isinstance(arg_type, UnknownType):
            return None

        # Unify argument type with parameter type
        if param.ty is None:
            return None

        success = _unify_types_for_inference(param.ty, arg_type, type_param_map)
        if not success:
            return None

    # Check that all type parameters were inferred
    for tp in generic_func.type_params:
        tp_name = tp.name if hasattr(tp, 'name') else str(tp)
        if tp_name not in type_param_map:
            return None

    # Extract type arguments in parameter order and resolve UnknownType
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    type_args = []
    for tp in generic_func.type_params:
        tp_name = tp.name if hasattr(tp, 'name') else str(tp)
        inferred_type = type_param_map[tp_name]
        # Resolve UnknownType to concrete StructType/EnumType if possible
        resolved_type = resolve_unknown_type(inferred_type, validator.struct_table, validator.enum_table)
        type_args.append(resolved_type)

    return tuple(type_args)


def _unify_types_for_inference(
    param_type: Type,
    arg_type: Type,
    type_param_map: Dict[str, Type]
) -> bool:
    """Unify parameter type with argument type for type inference.

    Args:
        param_type: Parameter type (may contain TypeParameter or UnknownType representing type param)
        arg_type: Argument type (concrete)
        type_param_map: Accumulator for type parameter assignments

    Returns:
        True if unification succeeds
    """
    from sushi_lang.semantics.generics.types import TypeParameter

    # Case 1: param_type is a type parameter
    if isinstance(param_type, TypeParameter):
        param_name = param_type.name

        if param_name in type_param_map:
            # Must match existing assignment
            return type_param_map[param_name] == arg_type
        else:
            # New assignment
            type_param_map[param_name] = arg_type
            return True

    # Case 2: param_type is UnknownType (might be a type parameter name)
    # This happens when the generic function parameter type is parsed as UnknownType("T")
    if isinstance(param_type, UnknownType):
        param_name = str(param_type)

        if param_name in type_param_map:
            # Must match existing assignment
            return type_param_map[param_name] == arg_type
        else:
            # New assignment
            type_param_map[param_name] = arg_type
            return True

    # Case 3: Both are concrete types - must match
    if param_type == arg_type:
        return True

    # Case 4: Nested generic types (e.g., Container<T>)
    # Handle GenericTypeRef unified with concrete monomorphized type
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(param_type, GenericTypeRef):
        param_base = param_type.base_name
        param_type_args = param_type.type_args

        # Check if arg_type is a monomorphized generic with metadata
        if isinstance(arg_type, (StructType, EnumType)):
            # Use generic metadata if available
            if arg_type.generic_base is not None and arg_type.generic_args is not None:
                # Check base names match
                if param_base != arg_type.generic_base:
                    return False

                # Check type argument counts match
                if len(param_type_args) != len(arg_type.generic_args):
                    return False

                # Recursively unify each type argument
                for param_arg, concrete_arg in zip(param_type_args, arg_type.generic_args):
                    if not _unify_types_for_inference(param_arg, concrete_arg, type_param_map):
                        return False

                return True

        # If arg_type is also a GenericTypeRef, unify them directly
        elif isinstance(arg_type, GenericTypeRef):
            arg_base = arg_type.base_name
            arg_type_args = arg_type.type_args

            # Base names must match
            if param_base != arg_base:
                return False

            # Type argument counts must match
            if len(param_type_args) != len(arg_type_args):
                return False

            # Recursively unify each type argument pair
            for param_arg, arg_arg in zip(param_type_args, arg_type_args):
                if not _unify_types_for_inference(param_arg, arg_arg, type_param_map):
                    return False

            return True

    return False


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
    for i, (arg, param) in enumerate(zip(actual_args, expected_params)):
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
