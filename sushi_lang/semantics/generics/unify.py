"""One type-unification engine, shared by the instantiate and typecheck passes."""
from __future__ import annotations

from typing import Dict, List, Optional

from sushi_lang.semantics.typesys import (
    Type,
    UnknownType,
    StructType,
    EnumType,
    FunctionType,
    ArrayType,
    DynamicArrayType,
    ReferenceType,
)
from sushi_lang.semantics.generics.types import TypeParameter, GenericTypeRef
from sushi_lang.semantics.generics.type_display import display_type


def unify_types(param_type: Type, arg_type: Type, type_param_map: Dict[str, Type],
                why: Optional[List[str]] = None) -> bool:
    """Unify a parameter type against a concrete argument type.

    `why` collects the reason for a miss that the caller can name to the user (CE2060).
    """
    # Case 0: reference parameter -- unify THROUGH the borrow (F7, 2026-08-14).
    # `peek T item` called as `f(peek x)` binds T from x's type; `peek Pair@(A, B)`
    # binds both. Mutability and the borrow spelling are argument-validation questions
    # (CE2006), not unification ones -- a mis-spelled call records an instantiation
    # that the typecheck pass then rejects, which is harmless.
    if isinstance(param_type, ReferenceType):
        inner_arg = (arg_type.referenced_type
                     if isinstance(arg_type, ReferenceType) else arg_type)
        return unify_types(param_type.referenced_type, inner_arg, type_param_map, why)

    if isinstance(param_type, TypeParameter):
        param_name = param_type.name
        if param_name in type_param_map:
            return type_param_map[param_name] == arg_type
        type_param_map[param_name] = arg_type
        return True

    if isinstance(param_type, UnknownType):
        param_name = str(param_type)
        if param_name in type_param_map:
            return type_param_map[param_name] == arg_type
        type_param_map[param_name] = arg_type
        return True

    if param_type == arg_type:
        return True

    if isinstance(param_type, GenericTypeRef):
        param_base = param_type.base_name
        param_type_args = param_type.type_args

        if isinstance(arg_type, (StructType, EnumType)):
            if arg_type.generic_base is not None and arg_type.generic_args is not None:
                if param_base != arg_type.generic_base:
                    return False
                if len(param_type_args) != len(arg_type.generic_args):
                    return False
                for param_arg, concrete_arg in zip(param_type_args, arg_type.generic_args, strict=False):
                    if not unify_types(param_arg, concrete_arg, type_param_map, why):
                        return False
                return True

        elif isinstance(arg_type, GenericTypeRef):
            if param_base != arg_type.base_name:
                return False
            if len(param_type_args) != len(arg_type.type_args):
                return False
            for param_arg, arg_arg in zip(param_type_args, arg_type.type_args, strict=False):
                if not unify_types(param_arg, arg_arg, type_param_map, why):
                    return False
            return True

    if isinstance(param_type, FunctionType) and isinstance(arg_type, FunctionType):
        if len(param_type.param_types) != len(arg_type.param_types):
            return False
        for p_param, a_param in zip(param_type.param_types, arg_type.param_types, strict=False):
            if not unify_types(p_param, a_param, type_param_map, why):
                return False
        return unify_types(param_type.ok_type, arg_type.ok_type, type_param_map, why)

    # Case 5: array types - unify the element type (issue #137: T[] -> T inference);
    # fixed-size arrays additionally require equal size.
    if isinstance(param_type, DynamicArrayType) and isinstance(arg_type, DynamicArrayType):
        return unify_types(param_type.base_type, arg_type.base_type, type_param_map, why)
    if isinstance(param_type, ArrayType) and isinstance(arg_type, ArrayType):
        if param_type.size != arg_type.size:
            if why is not None:
                why.append(f"the parameter is '{display_type(param_type)}', the argument "
                           f"is '{display_type(arg_type)}'")
            return False
        return unify_types(param_type.base_type, arg_type.base_type, type_param_map, why)

    return False
