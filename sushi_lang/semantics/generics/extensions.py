"""Generic Extension Method Monomorphization"""
from __future__ import annotations
from dataclasses import replace
from typing import Dict, Tuple, Set

from sushi_lang.semantics.ast import ExtendDef, Param
from sushi_lang.semantics.typesys import Type, StructType
from sushi_lang.semantics.generics.types import TypeParameter, GenericTypeRef
from sushi_lang.semantics.passes.collect import GenericExtensionMethod
from sushi_lang.internals.errors import raise_internal_error


def substitute_type_params(
    ty: Type,
    substitution: Dict[str, Type]
) -> Type:
    """Recursively substitute type parameters in a type annotation."""
    if isinstance(ty, TypeParameter):
        return substitution.get(ty.name, ty)

    if isinstance(ty, GenericTypeRef):
        new_type_args = tuple(
            substitute_type_params(arg, substitution)
            for arg in ty.type_args
        )
        return GenericTypeRef(base_name=ty.base_name, type_args=new_type_args)

    from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, FunctionType
    if isinstance(ty, ArrayType):
        new_base = substitute_type_params(ty.base_type, substitution)
        return ArrayType(base_type=new_base, size=ty.size)
    elif isinstance(ty, DynamicArrayType):
        new_base = substitute_type_params(ty.base_type, substitution)
        return DynamicArrayType(base_type=new_base)

    elif isinstance(ty, FunctionType):
        # `replace`, so `param_modes` rides along beside `captures` (#368).
        return replace(
            ty,
            param_types=tuple(substitute_type_params(p, substitution) for p in ty.param_types),
            ok_type=substitute_type_params(ty.ok_type, substitution),
            err_type=substitute_type_params(ty.err_type, substitution),
        )

    return ty


def monomorphize_extension_method(
    generic_method: GenericExtensionMethod,
    concrete_target_type: StructType,
    type_args: Tuple[Type, ...]
) -> ExtendDef:
    """Monomorphize a generic extension method for a specific instantiation."""
    if len(type_args) != len(generic_method.type_params):
        raise_internal_error("CE0096", operation=f"Type argument count mismatch: expected {len(generic_method.type_params)}, "
            f"got {len(type_args)}"
        )

    substitution = {}
    for param, arg in zip(generic_method.type_params, type_args, strict=False):
        param_name = param.name if hasattr(param, 'name') else param
        substitution[param_name] = arg

    concrete_ret_type = None
    if generic_method.ret_type is not None:
        concrete_ret_type = substitute_type_params(generic_method.ret_type, substitution)

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
            is_nom=getattr(param, 'is_nom', False),
            nom_span=getattr(param, 'nom_span', None),
        ))

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
    """Monomorphize all generic extension methods for all struct instantiations."""
    result: Dict[Tuple[str, str, Tuple[Type, ...]], ExtendDef] = {}

    for base_name, type_args in struct_instantiations:
        if base_name not in generic_extensions:
            continue

        concrete_type_name = f"{base_name}<{', '.join(str(t) for t in type_args)}>"
        concrete_struct = monomorphized_structs.get(concrete_type_name)
        if concrete_struct is None:
            continue

        for method_name, generic_method in generic_extensions[base_name].items():
            concrete_method = monomorphize_extension_method(
                generic_method,
                concrete_struct,
                type_args
            )

            key = (concrete_type_name, method_name, type_args)
            result[key] = concrete_method

    return result
