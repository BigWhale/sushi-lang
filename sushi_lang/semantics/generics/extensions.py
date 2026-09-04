"""Generic Extension Method Monomorphization"""
from __future__ import annotations
from typing import Dict, Tuple, Set, TYPE_CHECKING

from sushi_lang.semantics.ast import ExtendDef, Param
from sushi_lang.semantics.typesys import DynamicArrayType, EnumType, Type, StructType
from sushi_lang.semantics.generics.types import substitute_type_params
from sushi_lang.semantics.generics.extension_targets import instantiation_key
from sushi_lang.semantics.passes.collect import GenericExtensionMethod
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor


def monomorphize_extension_method(
    generic_method: GenericExtensionMethod,
    concrete_target_type: StructType | EnumType | DynamicArrayType | Type,
    type_args: Tuple[Type, ...],
    substitutor: "TypeSubstitutor",
    method_type_args: Tuple[Type, ...] = (),
) -> ExtendDef:
    """Monomorphize a generic extension method for a specific instantiation.

    The substitutor is REQUIRED, and it is what gives this instantiation its own body.
    An optional one would default to sharing the template's body with every other
    instantiation, which is the defect (#391): the typecheck pass's stamps and the borrow pass's ownership
    decisions are per instantiation and would land on the same nodes.
    """
    if len(type_args) != len(generic_method.type_params):
        raise_internal_error("CE0096", operation=f"Type argument count mismatch: expected {len(generic_method.type_params)}, "
            f"got {len(type_args)}"
        )

    substitution = {}
    for param, arg in zip(generic_method.type_params, type_args, strict=False):
        param_name = param.name if hasattr(param, 'name') else param
        substitution[param_name] = arg

    # Method-level type arguments (`name@(U)`, solved at the call site) compose with
    # the receiver substitution in this ONE pass over the original template body.
    method_type_params = getattr(generic_method, "method_type_params", ()) or ()
    if len(method_type_args) != len(method_type_params):
        raise_internal_error("CE0096", operation=(
            f"Method type argument count mismatch: expected {len(method_type_params)}, "
            f"got {len(method_type_args)}"))
    for param_name, arg in zip(method_type_params, method_type_args, strict=True):
        substitution[param_name] = arg

    concrete_ret_type = None
    if generic_method.ret_type is not None:
        concrete_ret_type = substitute_type_params(generic_method.ret_type, substitution)

    concrete_err_type = None
    if getattr(generic_method, "err_type", None) is not None:
        concrete_err_type = substitute_type_params(generic_method.err_type, substitution)

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
        # This instantiation's OWN body, with the type parameter substituted through it.
        body=substitutor.substitute_body(generic_method.body, substitution),
        loc=generic_method.loc,
        target_type_span=generic_method.target_type_span,
        name_span=generic_method.name_span,
        ret_span=generic_method.ret_span,
        err_type=concrete_err_type,
        err_span=getattr(generic_method, "err_span", None),
        method_type_args=tuple(method_type_args),
        # The receiver's MODE is part of the signature, so it has to survive the copy.
        # Losing it made every generic-target method's `self` by value: a `poke self`
        # write reached a private copy, and a `nom self` receiver registered as a borrow
        # so a field take out of it answered CE2411 (ruling R28).
        self_mode=getattr(generic_method, "self_mode", None),
        # Whether there IS a receiver at all (#542) -- the same reasoning: a copy that
        # lost the flag would be declared with a `self` parameter its body never
        # names, and every call site would be one argument short.
        is_static=bool(getattr(generic_method, "is_static", False)),
    )


def monomorphize_all_extension_methods(
    generic_extensions: Dict[str, Dict[Tuple[str, str], GenericExtensionMethod]],
    struct_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
    monomorphized_structs: Dict[str, StructType],
    enum_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
    monomorphized_enums: Dict[str, EnumType],
    substitutor: "TypeSubstitutor",
) -> Dict[Tuple[str, str, Tuple[Type, ...]], ExtendDef]:
    """Monomorphize the generic extension methods that APPLY to each instantiation.

    A concrete target argument is a constraint, so `extend Box@(i32)` produces one copy, for
    `Box<i32>` (#393). Every declaration used to be substituted positionally into every
    instantiation of the base name, which is what made the declared `i32` constrain nothing:
    the method answered a `Box@(string)` receiver, and its body reached the backend with a
    string where it had written an integer.

    An ENUM instantiation walks the same loop (#394): the loop used to read the struct
    tables only, so an extension on a generic enum target compiled into dead code and every
    call was CE2008.
    """
    result: Dict[Tuple[str, str, Tuple[Type, ...]], ExtendDef] = {}

    instantiation_sources = (
        (struct_instantiations, monomorphized_structs),
        (enum_instantiations, monomorphized_enums),
    )
    for instantiations, monomorphized_types in instantiation_sources:
        for base_name, type_args in instantiations:
            declarations = generic_extensions.get(base_name)
            if not declarations:
                continue

            concrete_type_name = instantiation_key(base_name, type_args)
            concrete_target = monomorphized_types.get(concrete_type_name)
            if concrete_target is None:
                continue

            for (method_name, target_key), generic_method in declarations.items():
                if target_key and target_key != concrete_type_name:
                    continue

                # A method-generic template cannot be monomorphized from the target
                # instantiation alone -- the CALL SITE names its method arguments, so
                # the typecheck pass queues it instead.
                if getattr(generic_method, "method_type_params", ()):
                    continue

                # A concrete target has no type parameters, so it substitutes nothing -- its
                # signature and body are already written in terms of the type it names.
                substitution_args = () if target_key else type_args

                concrete_method = monomorphize_extension_method(
                    generic_method,
                    concrete_target,
                    substitution_args,
                    substitutor=substitutor,
                )

                key = (concrete_type_name, method_name, type_args)
                result[key] = concrete_method

    return result


def monomorphize_perk_impl(
    template,
    concrete_target_type: StructType | EnumType | Type,
    type_args: Tuple[Type, ...],
    substitutor: "TypeSubstitutor",
):
    """One instantiation's copy of a generic-target perk implementation.

    The same substitution `monomorphize_extension_method` applies to one method, applied
    to every method of the implementation and to the target it names. The copy is an
    ordinary `ExtendWithDef` over a concrete type: nothing downstream -- the typecheck
    pass, the backend, the perk-impl table -- can tell it from a hand-written one.
    """
    import copy as _copy
    from sushi_lang.semantics.ast import ExtendWithDef

    if len(type_args) != len(template.type_params):
        raise_internal_error("CE0096", operation=(
            f"Type argument count mismatch: expected {len(template.type_params)}, "
            f"got {len(type_args)}"))

    substitution = {name: arg
                    for name, arg in zip(template.type_params, type_args, strict=True)}

    methods = []
    for method in template.impl.methods:
        concrete = _copy.copy(method)
        concrete.ret = (substitute_type_params(method.ret, substitution)
                        if method.ret is not None else None)
        concrete.err_type = (
            substitute_type_params(method.err_type, substitution)
            if getattr(method, "err_type", None) is not None else None)
        concrete.params = [
            Param(name=param.name,
                  ty=(substitute_type_params(param.ty, substitution)
                      if param.ty is not None else None),
                  name_span=param.name_span,
                  type_span=param.type_span,
                  is_nom=getattr(param, "is_nom", False),
                  nom_span=getattr(param, "nom_span", None))
            for param in method.params
        ]
        # This instantiation's OWN body, for the reason `monomorphize_extension_method`
        # states: the typecheck pass's stamps and the borrow pass's decisions are per
        # instantiation and would otherwise land on one shared set of nodes (#391).
        concrete.body = substitutor.substitute_body(method.body, substitution)
        methods.append(concrete)

    return ExtendWithDef(
        target_type=concrete_target_type,
        perk_name=template.impl.perk_name,
        methods=methods,
        target_type_span=template.impl.target_type_span,
        perk_name_span=template.impl.perk_name_span,
        loc=template.impl.loc,
        doc=template.impl.doc,
    )


def monomorphize_all_perk_impls(
    generic_perk_impls,
    struct_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
    monomorphized_structs: Dict[str, StructType],
    enum_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
    monomorphized_enums: Dict[str, EnumType],
    substitutor: "TypeSubstitutor",
) -> Dict[Tuple[str, str], object]:
    """Every generic-target perk implementation, once per instantiation that exists.

    The loop `monomorphize_all_extension_methods` walks, over the implementations rather
    than the methods. A base name with no instantiation in the program produces nothing,
    which is what makes an unused `BufReader@(R)` cost nothing.
    """
    result: Dict[Tuple[str, str], object] = {}
    if not generic_perk_impls:
        return result

    sources = (
        (struct_instantiations, monomorphized_structs),
        (enum_instantiations, monomorphized_enums),
    )
    for instantiations, monomorphized_types in sources:
        for base_name, type_args in instantiations:
            templates = generic_perk_impls.templates(base_name)
            if not templates:
                continue
            concrete_type_name = instantiation_key(base_name, type_args)
            concrete_target = monomorphized_types.get(concrete_type_name)
            if concrete_target is None:
                continue
            for template in templates:
                if len(template.type_params) != len(type_args):
                    continue
                key = (concrete_type_name, template.impl.perk_name)
                if key in result:
                    continue
                result[key] = (
                    template,
                    monomorphize_perk_impl(template, concrete_target, type_args,
                                           substitutor=substitutor),
                )

    return result
