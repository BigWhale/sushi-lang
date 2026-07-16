# semantics/generics/unify.py
"""Single type-unification engine shared by Pass 1.5 and Pass 2.

Instantiation collection (``TypeInferrer.unify_types``) and call-site inference
(``_unify_types_for_inference``) were hand-synced twins of this routine. They now
both delegate here so a new ``Type`` variant is handled in one place.
"""
from __future__ import annotations

from typing import Dict

from sushi_lang.semantics.typesys import (
    Type,
    UnknownType,
    StructType,
    EnumType,
    FunctionType,
    ArrayType,
    DynamicArrayType,
)
from sushi_lang.semantics.generics.types import TypeParameter, GenericTypeRef


def unify_types(param_type: Type, arg_type: Type, type_param_map: Dict[str, Type]) -> bool:
    """Unify a parameter type against a concrete argument type.

    Extracts type-parameter assignments into ``type_param_map`` as a side effect.
    A type parameter may appear either as a ``TypeParameter`` or as an
    ``UnknownType`` carrying the parameter name.

    Returns True if unification succeeds.
    """
    # Case 1: param_type is a type parameter
    if isinstance(param_type, TypeParameter):
        param_name = param_type.name
        if param_name in type_param_map:
            return type_param_map[param_name] == arg_type
        type_param_map[param_name] = arg_type
        return True

    # Case 1b: param_type is UnknownType (may be a type-parameter name, e.g. "T")
    if isinstance(param_type, UnknownType):
        param_name = str(param_type)
        if param_name in type_param_map:
            return type_param_map[param_name] == arg_type
        type_param_map[param_name] = arg_type
        return True

    # Case 2: both concrete - must match exactly
    if param_type == arg_type:
        return True

    # Case 3: nested generic types (e.g. Container<T>)
    if isinstance(param_type, GenericTypeRef):
        param_base = param_type.base_name
        param_type_args = param_type.type_args

        # arg_type is a monomorphized generic carrying its base/args metadata
        if isinstance(arg_type, (StructType, EnumType)):
            if arg_type.generic_base is not None and arg_type.generic_args is not None:
                if param_base != arg_type.generic_base:
                    return False
                if len(param_type_args) != len(arg_type.generic_args):
                    return False
                for param_arg, concrete_arg in zip(param_type_args, arg_type.generic_args, strict=False):
                    if not unify_types(param_arg, concrete_arg, type_param_map):
                        return False
                return True

        # arg_type is itself a GenericTypeRef - unify directly
        elif isinstance(arg_type, GenericTypeRef):
            if param_base != arg_type.base_name:
                return False
            if len(param_type_args) != len(arg_type.type_args):
                return False
            for param_arg, arg_arg in zip(param_type_args, arg_type.type_args, strict=False):
                if not unify_types(param_arg, arg_arg, type_param_map):
                    return False
            return True

    # Case 4: function types (fn(T) -> U) - unify each parameter and the return type,
    # the enabler for generic higher-order functions like map<T, U>(List<T>, fn(T) -> U).
    if isinstance(param_type, FunctionType) and isinstance(arg_type, FunctionType):
        if len(param_type.param_types) != len(arg_type.param_types):
            return False
        for p_param, a_param in zip(param_type.param_types, arg_type.param_types, strict=False):
            if not unify_types(p_param, a_param, type_param_map):
                return False
        return unify_types(param_type.ok_type, arg_type.ok_type, type_param_map)

    # Case 5: array types - unify the element type (issue #137: T[] -> T inference);
    # fixed-size arrays additionally require equal size.
    if isinstance(param_type, DynamicArrayType) and isinstance(arg_type, DynamicArrayType):
        return unify_types(param_type.base_type, arg_type.base_type, type_param_map)
    if isinstance(param_type, ArrayType) and isinstance(arg_type, ArrayType):
        if param_type.size != arg_type.size:
            return False
        return unify_types(param_type.base_type, arg_type.base_type, type_param_map)

    return False
