"""Generic Extension Method Monomorphization"""
from __future__ import annotations
from dataclasses import replace
from typing import Callable, Dict, Iterator, Optional, Tuple, Set, TYPE_CHECKING

from sushi_lang.semantics.ast import ExtendDef
from sushi_lang.semantics.typesys import DynamicArrayType, EnumType, Type, StructType
from sushi_lang.semantics.generics.types import substitute_type_params
from sushi_lang.semantics.generics.monomorphize.transformer import substituted_param
from sushi_lang.semantics.generics.extension_targets import instantiation_key
from sushi_lang.semantics.passes.collect import GenericExtensionMethod
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor


def substitute_signature(decl, substitution: Dict[str, Type], substitutor: "TypeSubstitutor"):
    """The ONE copy of a template method with its type parameters substituted (#803).

    `decl` is an `ExtendDef` or a perk-implementation `FuncDef`. The copy is `decl`
    with its parameter types, its return, its channel and its body substituted; every
    other field is kept, because a copy that spells out what it keeps drops what it
    forgets. A method is never variadic (CE0115), so no parameter fans out.

    The signature takes the PURE substitution, which interns nothing: a copy cut after
    `resolve` and `derive` hands the instantiations its signature names to the late
    interner, and a type interned here would arrive there already published and be
    skipped. The body is this instantiation's OWN: the typecheck pass's stamps and the
    borrow pass's decisions are per instantiation (#391).
    """
    def sub(ty: Optional[Type]) -> Optional[Type]:
        return substitute_type_params(ty, substitution) if ty is not None else None

    return replace(
        decl,
        params=[substituted_param(param, sub(param.ty)) for param in decl.params],
        ret=sub(decl.ret),
        err_type=sub(decl.err_type),
        body=substitutor.substitute_body(decl.body, substitution),
        # A copy is an instance: its method-level type parameters are solved.
        type_params=None,
    )


def _type_substitution(names, type_args: Tuple[Type, ...]) -> Dict[str, Type]:
    """Type parameter name -> argument. The collect pass refuses a wrong count (CE2062, #796)."""
    names = [getattr(name, "name", name) for name in names]
    if len(type_args) != len(names):
        raise_internal_error("CE0000", detail=(
            f"type argument count mismatch: expected {len(names)}, got {len(type_args)}"))
    return dict(zip(names, type_args, strict=True))


def for_each_instantiation(sources, lookup: Callable[[str], object]) -> Iterator[tuple]:
    """Each instantiation that exists and that `lookup` holds templates for.

    `sources` pairs the instantiations with the table of their monomorphized types, the
    struct pair and then the enum pair (#394). Yields the type arguments, the interned
    name, the concrete target and what `lookup` answered for the base name.
    """
    for instantiations, monomorphized_types in sources:
        for base_name, type_args in instantiations:
            templates = lookup(base_name)
            if not templates:
                continue
            concrete_type_name = instantiation_key(base_name, type_args)
            concrete_target = monomorphized_types.get(concrete_type_name)
            if concrete_target is None:
                continue
            yield type_args, concrete_type_name, concrete_target, templates


def monomorphize_extension_method(
    generic_method: GenericExtensionMethod,
    concrete_target_type: StructType | EnumType | DynamicArrayType | Type,
    type_args: Tuple[Type, ...],
    substitutor: "TypeSubstitutor",
    method_type_args: Tuple[Type, ...] = (),
) -> ExtendDef:
    """Monomorphize a generic extension method for a specific instantiation.

    The substitutor is REQUIRED, and it is what gives this instantiation its own body.
    Method-level type arguments (`name@(U)`, solved at the call site) compose with the
    receiver substitution in this ONE pass over the template.
    """
    substitution = _type_substitution(generic_method.type_params, type_args)
    substitution.update(_type_substitution(generic_method.method_type_params,
                                           method_type_args))
    concrete = substitute_signature(generic_method.decl, substitution, substitutor)
    concrete.target_type = concrete_target_type
    concrete.method_type_args = tuple(method_type_args)
    # Where the source wrote no name or no return, the collected record points a
    # diagnostic at the declaration instead.
    concrete.name_span = concrete.name_span or generic_method.name_span
    concrete.ret_span = concrete.ret_span or generic_method.ret_span
    return concrete


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
    """
    result: Dict[Tuple[str, str, Tuple[Type, ...]], ExtendDef] = {}
    sources = ((struct_instantiations, monomorphized_structs),
               (enum_instantiations, monomorphized_enums))
    for type_args, concrete_type_name, concrete_target, declarations in (
            for_each_instantiation(sources, generic_extensions.get)):
        for (method_name, target_key), generic_method in declarations.items():
            if target_key and target_key != concrete_type_name:
                continue

            # A method-generic template cannot be monomorphized from the target
            # instantiation alone -- the CALL SITE names its method arguments, so
            # the typecheck pass queues it instead.
            if generic_method.method_type_params:
                continue

            # A concrete target has no type parameters, so it substitutes nothing -- its
            # signature and body are already written in terms of the type it names.
            substitution_args = () if target_key else type_args

            result[(concrete_type_name, method_name, type_args)] = monomorphize_extension_method(
                generic_method, concrete_target, substitution_args, substitutor=substitutor)

    return result


def monomorphize_perk_impl(
    template,
    concrete_target_type: StructType | EnumType | Type,
    type_args: Tuple[Type, ...],
    substitutor: "TypeSubstitutor",
):
    """One instantiation's copy of a generic-target perk implementation.

    `substitute_signature` applied to every method of the implementation, and the
    target it names. The copy is an ordinary `ExtendWithDef` over a concrete type -- the
    typecheck pass, the backend and the perk-impl table read it as one -- and it carries
    `is_synthesized`, so a walk over a unit's DECLARATIONS can tell it from the one a
    person wrote (#657).
    """
    substitution = _type_substitution(template.type_params, type_args)
    methods = [substitute_signature(method, substitution, substitutor)
               for method in template.impl.methods]
    # Each copy carries the template's spans, so a diagnostic in its body is told once
    # for all the instances, the rule of a generic function's instance (#648, #800).
    for method in methods:
        method.instance_of = method.name
    return replace(
        template.impl,
        target_type=concrete_target_type,
        methods=methods,
        is_synthesized=True,
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

    A base name with no instantiation in the program produces nothing, which is what
    makes an unused `BufReader@(R)` cost nothing.
    """
    result: Dict[Tuple[str, str], object] = {}
    if not generic_perk_impls:
        return result

    sources = ((struct_instantiations, monomorphized_structs),
               (enum_instantiations, monomorphized_enums))
    for type_args, concrete_type_name, concrete_target, templates in (
            for_each_instantiation(sources, generic_perk_impls.templates)):
        for template in templates:
            key = (concrete_type_name, template.impl.perk_name)
            if key in result:
                continue
            result[key] = (
                template,
                monomorphize_perk_impl(template, concrete_target, type_args,
                                       substitutor=substitutor),
            )

    return result
