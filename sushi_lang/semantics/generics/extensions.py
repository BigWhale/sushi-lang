"""Generic Extension Method Monomorphization"""
from __future__ import annotations
from contextlib import nullcontext
from dataclasses import replace
from typing import Callable, Dict, Iterator, Optional, Tuple, Set, TYPE_CHECKING

from sushi_lang.semantics.ast import ExtendDef
from sushi_lang.semantics.typesys import DynamicArrayType, EnumType, Type, StructType
from sushi_lang.semantics.generics.types import TemplateId, substitute_type_params
from sushi_lang.semantics.generics.monomorphize.transformer import substituted_param
from sushi_lang.semantics.generics.extension_targets import (
    extension_template_id, instantiation_key, perk_template_id, written_target)
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
    return substitute_header(decl, substitution,
                             substitutor.substitute_body(decl.body, substitution))


def substitute_header(decl, substitution: Dict[str, Type], body):
    """`substitute_signature` with the body given: the header half of the one copy.

    A template check asks a perk-implementation template for a method's signature over
    an opaque instance (#1070) and reads no body, so it hands an empty one.
    """
    def sub(ty: Optional[Type]) -> Optional[Type]:
        return substitute_type_params(ty, substitution) if ty is not None else None

    from sushi_lang.semantics.channel import has_channel
    return replace(
        decl,
        written_channel=has_channel(decl),
        params=[substituted_param(param, sub(param.ty)) for param in decl.params],
        ret=sub(decl.ret),
        err_type=sub(decl.err_type),
        body=body,
        # A copy is an instance: its method-level type parameters are solved.
        type_params=None,
    )


def _cutting(substitutor: "TypeSubstitutor", filename: Optional[str], target):
    """The copy of a method template in `filename` for the instance `target` is cut.

    The key of the copy is the interned name of its target type, the key a nested call
    of the copy names as its parent. A type instance that the body builds then has a
    site in the template (`Monomorphizer.written_at`).
    """
    monomorphizer = getattr(substitutor, "monomorphizer", None)
    if monomorphizer is None:
        return nullcontext()
    key = target.name if isinstance(target, (StructType, EnumType)) else None
    return monomorphizer.cutting(filename, key)


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
    substitution.update(_type_substitution(generic_method.method_type_param_names,
                                           method_type_args))
    with _cutting(substitutor, generic_method.filename, concrete_target_type):
        concrete = substitute_signature(generic_method.decl, substitution, substitutor)
    concrete.target_type = concrete_target_type
    concrete.method_type_args = tuple(method_type_args)
    concrete.home_unit = generic_method.unit_name
    # Adoption may send the copy to another unit; its symbol keeps the unit of the
    # template, which is the unit that a call of it reads.
    concrete.declaring_unit = generic_method.unit_name
    concrete.template_file = generic_method.filename
    # An instance names its target in full; the bounds belong to the template.
    concrete.target_params = ()
    # A copy of a template whose check refused it reports nothing (#1070).
    concrete.template_id, concrete.template_target = copy_identity(generic_method)
    # Where the source wrote no name or no return, the collected record points a
    # diagnostic at the declaration instead.
    concrete.name_span = concrete.name_span or generic_method.name_span
    concrete.ret_span = concrete.ret_span or generic_method.ret_span
    return concrete


def _error_arguments_hold(generic_method: GenericExtensionMethod, type_args,
                          site_key: str, substitutor: "TypeSubstitutor") -> bool:
    """E3 for one generic-target copy: no copy is cut for a refused type argument."""
    from sushi_lang.semantics.generics.monomorphize.functions import (
        callable_error_parameters)
    monomorphizer = getattr(substitutor, "monomorphizer", None)
    if not type_args or monomorphizer is None:
        return True
    error_params = callable_error_parameters(
        generic_method.ret_type, generic_method.err_type,
        generic_method.err_span or generic_method.ret_span, generic_method.params,
        generic_method.body, generic_method.name_span, generic_method.type_params)
    return monomorphizer.error_arguments_hold(
        generic_method.type_params, type_args, error_params, site_key,
        generic_method.filename)


def bounds_hold_for(template, type_args: Tuple[Type, ...], tables) -> bool:
    """Does an extension or perk template apply to the instance of these arguments?

    The target bounds of the template (#1070), asked by every cutter before it cuts: an
    instance that fails one gets no copy. A call of it is refused at the call (CE4006).
    """
    from sushi_lang.semantics.generics.constraints import target_bounds_hold
    return target_bounds_hold(template.target_bounds, type_args, tables.holds_bound)


def copy_identity(template: GenericExtensionMethod) -> Tuple[TemplateId, str]:
    """The template identity and the written target that each copy of `template` carries.

    One written declaration is one fault, so a diagnostic groups the copies by these two.
    """
    decl = template.decl
    written = (written_target(decl.target_type, decl.target_params)
               if decl is not None and decl.target_type is not None
               else template.base_type_name)
    return extension_template_id(template), written


def target_copy_args(template: GenericExtensionMethod, instance) -> Optional[Tuple[Type, ...]]:
    """The type arguments that a copy of `template` for `instance` substitutes, or None.

    The rules of the call-site rung (#1196). A method-generic template has no copy for
    the instance alone, because its call solves the method arguments. A concrete target
    substitutes nothing. A template whose parameter count is not the count of the
    instance's arguments gives the instance no copy.
    """
    if template.method_type_params:
        return None
    args = () if template.target_key else tuple(getattr(instance, "generic_args", None) or ())
    return args if len(template.type_params) == len(args) else None


def target_copy_substitution(template: GenericExtensionMethod, instance) -> Dict[str, Type]:
    """Type parameter name -> argument, for the copy of `template` for `instance`."""
    args = target_copy_args(template, instance)
    if args is None:
        raise_internal_error("CE0000", detail=(
            f"no copy of '{template.name}' applies to '{getattr(instance, 'name', instance)}'"))
    return _type_substitution(template.type_params, args)


def target_templates_of(generic_extensions, instance, method_name: str) -> list:
    """The generic-target templates that give `instance` the method, before the bounds.

    The ONE lookup of "which template gives this instance method M" (#1196). A copy of a
    generic-target method can be absent from the extension table, so each question about
    the methods of an instance asks here too. A concrete target comes before a template.
    Each unit gives one at most (C5); the calling unit chooses among them
    (`docs/design/extension-visibility.md`). The target bounds are the caller's: a call
    refuses a failed bound (CE4006), and a question about the type reads
    `target_methods_of`.
    """
    base = getattr(instance, "generic_base", None)
    if base is None:
        return []
    found = [template for template
             in generic_extensions.applicable(base, method_name, instance.name)
             if target_copy_args(template, instance) is not None]
    return sorted(found, key=lambda template: not template.target_key)


def target_methods_of(generic_extensions, instance, method_name: str, tables) -> list:
    """The generic-target templates that give `instance` the method, bounds included.

    What the instance HAS: a template whose target bound the instance fails gives it no
    method. A reader with no calling unit takes the first.
    """
    return [template for template
            in target_templates_of(generic_extensions, instance, method_name)
            if bounds_hold_for(template, target_copy_args(template, instance) or (), tables)]


def templates_by_instance(generic_extensions, instances, tables) -> Iterator[tuple]:
    """(instance, template) for each generic-target template that an instance has.

    The rule of `target_methods_of`, for each method name of each base. A check that
    judges a template at each instance reads it, because no copy exists until a call.
    """
    for instance in instances:
        base = getattr(instance, "generic_base", None)
        if base is None:
            continue
        names = dict.fromkeys(name for name, _key, _unit
                              in generic_extensions.by_type.get(base, {}))
        for name in names:
            for template in target_methods_of(generic_extensions, instance, name, tables):
                yield instance, template


def judge_error_arguments(generic_extensions, instances, substitutor: "TypeSubstitutor") -> None:
    """E3 for each generic-target template at each instance that has its method.

    No copy is cut here: a call cuts the copy (#1196). The judge stays per instance, so
    a type argument in an `E` position of a template stops the analysis at the site
    that named the instance, as a refused constraint does.
    """
    tables = substitutor.monomorphizer.tables
    for instance, template in templates_by_instance(generic_extensions, instances, tables):
        _error_arguments_hold(template, target_copy_args(template, instance) or (),
                              instance.name, substitutor)


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
    with _cutting(substitutor, template.filename, concrete_target_type):
        methods = [substitute_signature(method, substitution, substitutor)
                   for method in template.impl.methods]
    # Each copy carries the template's spans, so a diagnostic in its body is told once
    # for all the instances, the rule of a generic function's instance (#648, #800).
    template_id = perk_template_id(template)
    for method in methods:
        method.instance_of = method.name
        method.template_id = template_id
    return replace(
        template.impl,
        target_type=concrete_target_type,
        methods=methods,
        is_synthesized=True,
        target_params=(),
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
            if key in result or not bounds_hold_for(template, type_args,
                                                    substitutor.monomorphizer.tables):
                continue
            result[key] = (
                template,
                monomorphize_perk_impl(template, concrete_target, type_args,
                                       substitutor=substitutor),
            )

    return result
