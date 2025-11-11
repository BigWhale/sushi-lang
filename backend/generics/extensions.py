# backend/generics/extensions.py
"""
Generic Extension Method Monomorphization

This module handles the monomorphization of generic extension methods.
For each generic type instantiation (e.g., Box<i32>), this generates
concrete extension method definitions with type parameters substituted.

Example:
    Generic: extend Box<T> unwrap() T
    + Instantiation Box<i32>
    → Concrete: extend Box<i32> unwrap() i32

This integrates with the existing monomorphization infrastructure
(Pass 1.6) and generates ExtendDef AST nodes for the backend to compile.
"""
from __future__ import annotations
from typing import Dict, Tuple, Set, Optional
from dataclasses import dataclass

from semantics.ast import ExtendDef, Block, Param
from semantics.typesys import Type, StructType
from semantics.generics.types import TypeParameter, GenericTypeRef
from semantics.passes.collect import GenericExtensionMethod, ExtensionMethod
from internals.report import Reporter
from internals.errors import raise_internal_error


def substitute_type_params(
    ty: Type,
    substitution: Dict[str, Type]
) -> Type:
    """Recursively substitute type parameters in a type annotation.

    Args:
        ty: The type to substitute (may contain TypeParameter instances)
        substitution: Mapping from parameter name to concrete type

    Returns:
        Type with all TypeParameter instances replaced

    Example:
        substitute_type_params(TypeParameter("T"), {"T": BuiltinType.I32})
        → BuiltinType.I32
    """
    # Base case: TypeParameter - substitute it
    if isinstance(ty, TypeParameter):
        return substitution.get(ty.name, ty)

    # Recursive case: GenericTypeRef - substitute type args
    if isinstance(ty, GenericTypeRef):
        new_type_args = tuple(
            substitute_type_params(arg, substitution)
            for arg in ty.type_args
        )
        return GenericTypeRef(base_name=ty.base_name, type_args=new_type_args)

    # Recursive case: ArrayType, DynamicArrayType - substitute element type
    from semantics.typesys import ArrayType, DynamicArrayType
    if isinstance(ty, ArrayType):
        new_base = substitute_type_params(ty.base_type, substitution)
        return ArrayType(base_type=new_base, size=ty.size)
    elif isinstance(ty, DynamicArrayType):
        new_base = substitute_type_params(ty.base_type, substitution)
        return DynamicArrayType(base_type=new_base)

    # Default: return type as-is (BuiltinType, StructType, etc.)
    return ty


def monomorphize_extension_method(
    generic_method: GenericExtensionMethod,
    concrete_target_type: StructType,
    type_args: Tuple[Type, ...]
) -> ExtendDef:
    """Monomorphize a generic extension method for a specific instantiation.

    Args:
        generic_method: The generic extension method definition
        concrete_target_type: The concrete struct type (e.g., Box<i32>)
        type_args: Concrete type arguments (e.g., (BuiltinType.I32,))

    Returns:
        Concrete ExtendDef AST node with type parameters substituted

    Example:
        Generic: extend Box<T> unwrap() T
        Concrete target: Box<i32>
        Type args: (BuiltinType.I32,)
        → Result: extend Box<i32> unwrap() i32
    """
    # Build substitution mapping: param_name -> concrete_type
    if len(type_args) != len(generic_method.type_params):
        raise_internal_error("CE0096", operation="Type argument count mismatch: expected {len(generic_method.type_params)}, "
            f"got {len(type_args)}"
        )

    # Extract names from type parameters (handles both str and BoundedTypeParam)
    substitution = {}
    for param, arg in zip(generic_method.type_params, type_args):
        # param can be: str (legacy), TypeParameter, or BoundedTypeParam
        param_name = param.name if hasattr(param, 'name') else param
        substitution[param_name] = arg

    # Substitute type parameters in return type
    concrete_ret_type = None
    if generic_method.ret_type is not None:
        concrete_ret_type = substitute_type_params(generic_method.ret_type, substitution)

    # Substitute type parameters in parameter types
    concrete_params = []
    for param in generic_method.params:
        concrete_param_type = None
        if param.ty is not None:
            concrete_param_type = substitute_type_params(param.ty, substitution)

        concrete_params.append(Param(
            name=param.name,
            ty=concrete_param_type,
            name_span=param.name_span,
            type_span=param.type_span,
            index=param.index
        ))

    # Create concrete ExtendDef (preserving the body from generic method)
    return ExtendDef(
        target_type=concrete_target_type,
        name=generic_method.name,
        params=concrete_params,
        ret=concrete_ret_type,
        body=generic_method.body,  # Preserve the original body
        loc=generic_method.loc,
        target_type_span=generic_method.target_type_span,
        name_span=generic_method.name_span,
        ret_span=generic_method.ret_span
    )


def monomorphize_all_extension_methods(
    generic_extensions: Dict[str, Dict[str, GenericExtensionMethod]],
    struct_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
    monomorphized_structs: Dict[str, StructType]
) -> Dict[Tuple[str, str, Tuple[Type, ...]], ExtendDef]:
    """Monomorphize all generic extension methods for all struct instantiations.

    Args:
        generic_extensions: Table of generic extension methods by base type name
        struct_instantiations: Set of (base_name, type_args) for structs
        monomorphized_structs: Already-monomorphized concrete struct types

    Returns:
        Dict mapping (target_type_name, method_name, type_args) → ExtendDef

    Example:
        Input:
          - generic_extensions["Box"]["unwrap"] = extend Box<T> unwrap() T
          - struct_instantiations = {("Box", (BuiltinType.I32,))}
          - monomorphized_structs["Box<i32>"] = Box<i32>
        Output:
          - {("Box<i32>", "unwrap", (BuiltinType.I32,)): extend Box<i32> unwrap() i32}
    """
    result: Dict[Tuple[str, str, Tuple[Type, ...]], ExtendDef] = {}

    for base_name, type_args in struct_instantiations:
        # Check if this generic type has extension methods
        if base_name not in generic_extensions:
            continue

        # Get the concrete struct type
        concrete_type_name = f"{base_name}<{', '.join(str(t) for t in type_args)}>"
        concrete_struct = monomorphized_structs.get(concrete_type_name)
        if concrete_struct is None:
            # Struct not monomorphized yet (shouldn't happen)
            continue

        # Monomorphize all extension methods for this type
        for method_name, generic_method in generic_extensions[base_name].items():
            concrete_method = monomorphize_extension_method(
                generic_method,
                concrete_struct,
                type_args
            )

            # Store by (target_type_name, method_name, type_args)
            key = (concrete_type_name, method_name, type_args)
            result[key] = concrete_method

    return result
