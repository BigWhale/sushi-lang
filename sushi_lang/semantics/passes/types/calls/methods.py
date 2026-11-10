"""The resolution of a user-written method: a perk implementation, then an extension.

The order of the rungs is `calls/dotcall.py:resolve_dotted`, and the SHIR arms of an
extension method and of a static are in `visit/calls.py`.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import (
    DynamicArrayType, EnumType, StructType)
from sushi_lang.semantics.generics.types import TypeParameter
from sushi_lang.semantics.generics.constraints import target_bounds_hold
from sushi_lang.semantics.passes.collect.functions import GenericExtensionMethod
from ..utils import call_constraint_check, intern_signature

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.exprs import ShirExpr
    from .. import TypeValidator


def instantiate_array_extension(validator: 'TypeValidator',
                                receiver_type: DynamicArrayType, template, site=None):
    """Resolve a call on a `T[]` receiver against one array template (ruling 3).

    A dynamic-array receiver has no instantiation the monomorphize pass could have
    collected -- the CALL SITE is what names the element type -- so the call cuts the
    copy of the `$array` template that it chose. The substituted signature enters the
    extension table (the next call site hits it directly, in this pass and in the
    backend), and the instantiation is queued for the analyzer's fixpoint round, which
    monomorphizes and checks the body copy after the per-unit loop.
    """

    # The element as the tables know it: a receiver typed from a written `P[]` still
    # holds the bare name, and a copy for it checks a body over an unknown type (#1161).
    receiver_type = _resolved(validator, receiver_type)
    element = receiver_type.base_type
    # An element that fails a target bound gets no copy; the last rung refuses the call.
    if not target_bounds_hold(template.target_bounds, (element,),
                              validator.tables.holds_bound):
        return None
    concrete = substituted_extension_signature(
        validator, template, receiver_type, {template.type_params[0]: element})
    # A template check answers the signature and writes nothing (#1070).
    if not validator.in_template_check:
        validator.extension_table.add_method(concrete)
        _queue_extension_instantiation(validator, template, receiver_type, (element,), (),
                                       site)
    return concrete


def instantiate_target_extension(validator: 'TypeValidator', receiver_type, template,
                                 report: bool = True, site: Optional[Span] = None):
    """Resolve a call on an instance of a generic type against one generic-target template.

    The `T[]` rung is the model (#1196). The substituted signature answers the call. The
    first call that reaches the method files the signature in the extension table, so
    the next call and the backend find it, and queues the body copy for the analyzer's
    fixpoint round. A question with no `site` (the `foreach` protocol asks for `next()`)
    gets the signature and writes nothing; the call that `foreach` then validates writes
    it. A template check and an early inference write nothing either.

    An instance that fails a target bound gets no copy, and the last rung refuses the
    call (CE4006). A type argument in an `E` position of the template is CE2084 at the
    call (E3), told only with `report` and a site to point at.
    """
    from sushi_lang.semantics.generics.extensions import (
        bounds_hold_for, target_copy_args, target_copy_substitution)
    args = target_copy_args(template, receiver_type)
    if args is None or not bounds_hold_for(template, args, validator.tables):
        return None
    substitution = target_copy_substitution(template, receiver_type)
    report = report and site is not None
    if _refuses_non_error_arguments(validator, template, receiver_type, substitution,
                                    site, report):
        return RESOLUTION_REPORTED if report else None
    concrete = target_copy_signature(validator, template, receiver_type)
    if site is not None and validator.queues_late_copies:
        validator.extension_table.add_method(concrete)
        _queue_extension_instantiation(validator, template, receiver_type, args, (), site)
    return concrete


def _array_templates(validator: 'TypeValidator', method_name: str) -> list:
    """The `T[]` templates of this method name with no method-level parameter.

    One for each unit that declares one (`docs/design/extension-visibility.md` C5).
    """
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
    templates = validator.generic_extension_table.declarations(ARRAY_BASE_KEY, method_name)
    return [t for t in templates if not t.target_key and not t.method_type_params]


def _array_template(validator: 'TypeValidator', method_name: str):
    """The `T[]` template of this method name that the calling unit reads first, or None.

    Its own unit's template first, then a template that it may call, then any.
    """
    return next(iter(in_reach_order(validator, _array_templates(validator, method_name))),
                None)


def in_reach_order(validator: 'TypeValidator', templates: list, target=None) -> list:
    """The templates in the order the calling unit reads them: its own, then the ones
    that it may call (`extension_reach`), then the rest.

    `target` is the type the templates extend; its home unit gives R5. A template of an
    array has no home unit, so it needs none.
    """
    from sushi_lang.semantics.passes.types.visibility import calling_unit, extension_reaches
    from sushi_lang.semantics.visibility import MethodReach
    asker = calling_unit(validator)
    ranked = [(template.unit_name != asker, reach is not MethodReach.VISIBLE, index)
              for index, (template, reach)
              in enumerate(extension_reaches(validator, templates, target))]
    return [templates[index] for *_, index in sorted(ranked)]


def substituted_extension_signature(validator: 'TypeValidator', template, receiver_type,
                                    substitution):
    """The signature of an extension template with `substitution` put through it.

    The ONE builder, for every rung that answers a call from a template: an array
    template, a method-generic template, and a generic-target template in a template
    check (#1070). It interns what the signature names (risk 1) and writes no table.
    """
    from sushi_lang.semantics.generics.types import substitute_type_params
    from sushi_lang.semantics.passes.collect.functions import ExtensionMethod, Param

    ret = (substitute_type_params(template.ret_type, substitution)
           if template.ret_type is not None else None)
    err = (substitute_type_params(template.err_type, substitution)
           if getattr(template, "err_type", None) is not None else None)
    params = [Param(
        name=p.name,
        ty=substitute_type_params(p.ty, substitution) if p.ty is not None else None,
        name_span=p.name_span, type_span=p.type_span, index=p.index,
        is_variadic=getattr(p, "is_variadic", False),
        is_nom=getattr(p, "is_nom", False),
    ) for p in template.params]
    intern_signature(validator, ret, err, *(p.ty for p in params))
    return ExtensionMethod(
        target_type=receiver_type, name=template.name, params=params, ret_type=ret,
        loc=template.loc, target_type_span=template.target_type_span,
        name_span=template.name_span, ret_span=template.ret_span,
        self_mode=template.self_mode, filename=template.filename,
        unit_name=template.unit_name,
        err_type=err, err_span=getattr(template, "err_span", None),
        is_static=bool(getattr(template, "is_static", False)),
        is_public=bool(getattr(template, "is_public", False)),
        borrowed_return=getattr(template, "borrowed_return", None))


def target_copy_signature(validator: 'TypeValidator', template, instance):
    """The signature of the copy of a generic-target template for `instance`.

    `substituted_extension_signature` with the template identity that each copy carries
    (`copy_identity`). The ONE builder for an instance method and a static alike.
    """
    from sushi_lang.semantics.generics.extensions import (
        copy_identity, target_copy_substitution)
    concrete = substituted_extension_signature(
        validator, template, instance, target_copy_substitution(template, instance))
    concrete.template_id, concrete.template_target = copy_identity(template)
    return concrete


def _resolved(validator: 'TypeValidator', ty):
    """The type with every name in it resolved to its table entry."""
    from sushi_lang.semantics.type_resolution import resolve_type_recursively
    return resolve_type_recursively(ty, validator.struct_table.by_name,
                                    validator.enum_table.by_name)


@dataclass
class CopyRequest:
    """One call-site extension copy that the analyzer's fixpoint round cuts.

    `key` names the copy, `parent` is the key of the copy whose body made the call (None
    for a written body), and `site` is the (span, file) of the call. The growth rule
    reads the three (`generics/instance_growth.py`).
    """
    template: object
    target_type: object
    receiver_args: tuple
    method_type_args: tuple
    key: tuple
    parent: object
    site: tuple


def _queue_extension_instantiation(validator: 'TypeValidator', template, target_type,
                                   receiver_args, method_type_args, site=None) -> None:
    """Queue one monomorphization request, deduped by (receiver, method, margs).

    The key holds the resolved TYPES, never their text: `P[]` with the element unknown
    and `P[]` with the element resolved print the same, and the first request hid the
    correct one (#1161). `site` is the span of the call.
    """
    key = (_resolved(validator, target_type), template.name,
           tuple(_resolved(validator, a) for a in method_type_args), template.unit_name)
    tables = validator.tables
    if key in tables.queued_extension_keys:
        return
    tables.queued_extension_keys.add(key)
    tables.pending_extension_instantiations.append(CopyRequest(
        template, target_type, tuple(receiver_args), tuple(method_type_args),
        key=("method", *key), parent=getattr(validator, "body_instance_key", None),
        site=(site, validator.reporter.filename)))


# Returned when a rung found the template but the call cannot use it (the rung reported
# the fault): the caller must not add CE2008 on top.
RESOLUTION_REPORTED = object()


@dataclass(frozen=True)
class ResolvedMethod:
    """A user-written method a receiver answers to, and which table answered."""
    method: object


def resolve_extension_method(validator: 'TypeValidator', receiver_type,
                             method_name: str, report: bool = True,
                             static: bool = False, site: Optional[Span] = None):
    """The EXTENSION rungs, in the order every caller reads them.

    The extension table, then a `T[]` template instantiated at this call site, then a
    template of a generic target. Returns None, the method, or RESOLUTION_REPORTED when a
    rung reported the fault and the caller must not add CE2008 on top.

    `static` says which of the two callable shapes is being asked for (#542). The two
    share one table, keyed on (type, name), so ONE filter keeps them apart: an instance
    call may not reach a static, and a call on the type name may not reach an instance
    method. Without it a static was called with a receiver it never declared.

    Whatever a rung answers, the calling unit must be allowed to call it
    (`docs/design/extension-visibility.md`): `_choose_visible` reads the one predicate.
    A refusal stands at `site`. The method-generic rung does not run here: the arm of the
    call asks it after this answer (`visit/method_templates.py`).
    """
    records = validator.extension_table.declarations(receiver_type, method_name)
    templates = _uncut_templates(validator, receiver_type, method_name, records)
    method = None
    if records or templates:
        matching = [record for record in [*records, *templates]
                    if bool(getattr(record, "is_static", False)) == static]
        if not matching:
            return None
        chosen = _choose_visible(validator, receiver_type, method_name, matching, site,
                                 report)
        if not isinstance(chosen, GenericExtensionMethod):
            return chosen
        if isinstance(receiver_type, DynamicArrayType):
            method = instantiate_array_extension(validator, receiver_type, chosen, site)
        else:
            method = instantiate_target_extension(validator, receiver_type, chosen, report,
                                                  site)
            if method is RESOLUTION_REPORTED:
                return method
        if method is not None:
            return method

    if method is None:
        method = _answer_from_template(validator, receiver_type, method_name, site,
                                       report=report and site is not None, static=static)
        if method is RESOLUTION_REPORTED:
            return RESOLUTION_REPORTED

    if method is None or method is RESOLUTION_REPORTED:
        return method
    if bool(getattr(method, "is_static", False)) != static:
        return None
    return _choose_visible(validator, receiver_type, method_name, [method], site, report)


def _uncut_templates(validator: 'TypeValidator', receiver_type, method_name: str,
                     records: list) -> list:
    """The templates of a unit that has no copy for this receiver yet.

    A `T[]` template, or a generic-target template of the receiver's base. A copy is cut
    at the call that reaches it, so the extension table holds only the copies that a
    call has cut. A unit with no copy yet is still a candidate of the call (C5). An
    instance over an opaque parameter has no copy; the last rung answers it.
    """
    from sushi_lang.semantics.generics.extensions import target_templates_of
    from sushi_lang.semantics.generics.opaque import holds_opaque
    if isinstance(receiver_type, DynamicArrayType):
        templates = _array_templates(validator, method_name)
    elif isinstance(receiver_type, (StructType, EnumType)) and not holds_opaque(receiver_type):
        templates = target_templates_of(validator.generic_extension_table, receiver_type,
                                        method_name)
    else:
        return []
    cut = {record.unit_name for record in records}
    return [t for t in templates if t.unit_name not in cut]


def _choose_visible(validator: 'TypeValidator', receiver_type, method_name: str,
                    records: list, site: Optional[Span], report: bool):
    """The one extension of `records` that the calling unit calls, or a refusal.

    An own extension wins (C3, C5). One visible extension answers. Two or more visible
    ones are CE3023 at the call (C4). No visible one is CE3022 or CE3005. A refusal is
    reported only with `report` and a call site to point at, and answers RESOLUTION_REPORTED,
    so the caller adds no CE2008. A reader with no unit of its own takes the first.
    """
    from sushi_lang.semantics.passes.types.visibility import (
        calling_unit, extension_reaches, reject_ambiguous_extension,
        reject_unreachable_extension)
    from sushi_lang.semantics.visibility import MethodReach

    asker = calling_unit(validator)
    if asker is None:
        return records[0]
    reaches = extension_reaches(validator, records, receiver_type)
    visible = [record for record, reach in reaches if reach is MethodReach.VISIBLE]
    own = [record for record in visible if record.unit_name == asker]
    if own:
        return own[0]
    if len(visible) == 1:
        return visible[0]
    if not (report and site is not None):
        return None
    if visible:
        reject_ambiguous_extension(validator, receiver_type, method_name, visible, site)
    else:
        reject_unreachable_extension(validator, receiver_type, method_name, reaches, site)
    return RESOLUTION_REPORTED


def _answer_from_template(validator: 'TypeValidator', receiver_type, method_name: str,
                          site, *, report: bool, static: bool = False):
    """The last rung: a template that applies to a receiver with no copy (#1070).

    An extension template of the receiver's base, or a perk template of it that gives
    the method. When the template's target bounds fail for the receiver, no copy was
    cut: with `report` the call is CE4006 at `site`, with the note at the bound (the
    rule of a free function), and the answer is RESOLUTION_REPORTED; without, None. In
    a template check, an extension template answers an instance over an opaque
    parameter with its substituted signature, and nothing is written. Every other
    receiver whose bounds hold has its copy already, so the answer is None.
    """
    from sushi_lang.semantics.generics.opaque import holds_opaque
    found = _template_for(validator, receiver_type, method_name)
    if found is None:
        return None
    template, args, is_static = found
    if is_static != static:
        return None
    if not target_bounds_hold(template.target_bounds, args, validator.tables.holds_bound):
        if not report:
            return None
        _refuses_unmet_bounds(validator, template, method_name, receiver_type, args, (),
                              site, report=True)
        return RESOLUTION_REPORTED
    if (not validator.in_template_check or not holds_opaque(receiver_type)
            or not isinstance(template, GenericExtensionMethod)):
        return None
    names = [p.name if hasattr(p, "name") else p for p in template.type_params]
    return substituted_extension_signature(validator, template, receiver_type,
                                           dict(zip(names, args, strict=True)))


def _template_for(validator: 'TypeValidator', receiver_type, method_name: str):
    """The template that gives this receiver the method: (template, its arguments, static).

    An extension template first, then a perk template of the same base. A
    method-generic template is not one: its call solves the method arguments first. A
    generic-target template is the one lookup's (`target_templates_of`), in the order
    that the calling unit reads them.
    """
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
    from sushi_lang.semantics.generics.extensions import target_copy_args, target_templates_of
    base: Optional[str] = None
    if isinstance(receiver_type, DynamicArrayType):
        base = ARRAY_BASE_KEY
        args: tuple = (receiver_type.base_type,)
        template = _array_template(validator, method_name)
        if (template is not None and not template.method_type_params
                and len(template.type_params) == len(args)):
            return template, args, template.is_static
    else:
        base = getattr(receiver_type, "generic_base", None)
        args = tuple(getattr(receiver_type, "generic_args", None) or ())
        if base is None:
            return None
        candidates = target_templates_of(validator.generic_extension_table, receiver_type,
                                         method_name)
        template = next(iter(in_reach_order(validator, candidates, receiver_type)), None)
        if template is not None:
            return template, target_copy_args(template, receiver_type), template.is_static
    for perk_template in validator.tables.generic_perk_impls.templates(base):
        if (len(perk_template.type_params) == len(args)
                and any(m.name == method_name for m in perk_template.impl.methods)):
            return perk_template, args, False
    return None


def resolve_method(validator: 'TypeValidator', receiver_type, method_name: str,
                   report: bool = True, static: bool = False):
    """The whole ladder for a USER-written method: a perk implementation, then extensions.

    A perk implementation is the sanctioned override and wins
    (`docs/design/method-resolution.md`), so it is asked first. Built-in methods are NOT
    on this ladder: each family resolves its own before the ladder is reached, and the
    claims of the families are disjoint (`tests/unit/test_method_family_dispatch_is_one.py`).

    Returns a `ResolvedMethod`, None, or RESOLUTION_REPORTED. The `foreach` protocol and
    the static path read it, and the point of one function is that a third cannot drift.

    `static` skips the perk rung outright: a perk holds no static (CE4014), so a
    contract can never answer a call written on the type name.
    """
    if not static:
        perk_method = validator.perk_impl_table.get_method(receiver_type, method_name)
        if perk_method is not None:
            return ResolvedMethod(method=perk_method)

    method = resolve_extension_method(validator, receiver_type, method_name,
                                      report=report, static=static)
    if method is RESOLUTION_REPORTED:
        return RESOLUTION_REPORTED
    if method is None:
        return None
    return ResolvedMethod(method=method)


def find_method_generic_template(validator: 'TypeValidator', receiver_type,
                                 method_name: str, site: Optional[Span], report: bool):
    """The method-generic template a receiver answers to, plus its receiver substitution.

    Three receiver shapes: a `T[]` (the `$array` templates, element substitution), an
    instantiation of a generic base (`List<i32>` -- base-name lookup, args from the
    interned type), and any concrete type (its display name is the base key, empty
    substitution). Returns (None, None) when no margs template answers.

    Each unit can declare one template of a name (C5), so the calling unit chooses
    among them through `_choose_visible`: its own, else the one it may call. Two that
    it may call are CE3023 (C4), and the answer is (RESOLUTION_REPORTED, None).
    """
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY

    def margs_templates(declarations, target_key=""):
        return [t for t in declarations
                if getattr(t, "method_type_params", ())
                and not (t.target_key and t.target_key != target_key)]

    def choose(templates):
        return _choose_visible(validator, receiver_type, method_name, templates, site, report)

    table = validator.generic_extension_table
    if isinstance(receiver_type, DynamicArrayType):
        templates = margs_templates(table.declarations(ARRAY_BASE_KEY, method_name))
        if templates:
            template = choose(templates)
            if not isinstance(template, GenericExtensionMethod):
                return template, None
            subst = ({template.type_params[0]: receiver_type.base_type}
                     if template.type_params else {})
            return template, subst
        # A concrete-element array template files under its display name below.

    generic_base = getattr(receiver_type, "generic_base", None)
    if generic_base is not None:
        templates = margs_templates(table.declarations(generic_base, method_name),
                                    target_key=getattr(receiver_type, "name", ""))
        if templates:
            template = choose(templates)
            if not isinstance(template, GenericExtensionMethod):
                return template, None
            names = [p.name if hasattr(p, "name") else p for p in template.type_params]
            args = getattr(receiver_type, "generic_args", None) or ()
            subst = dict(zip(names, args, strict=False)) if not template.target_key else {}
            return template, subst

    templates = margs_templates(table.declarations(display_type(receiver_type), method_name))
    if templates:
        template = choose(templates)
        if not isinstance(template, GenericExtensionMethod):
            return template, None
        return template, {}
    return None, None


def unsolved_margs_help(bare_lambda: bool, count: int) -> str:
    """The help of CE2063, from its cause: a bare-param lambda, or no argument at all."""
    if bare_lambda:
        return ("annotate the lambda's parameter types ('|i32 x| ...'), or pass a named "
                "function -- a bare-param lambda has no type of its own to infer from")
    pronoun = "them" if count > 1 else "it"
    return (f"no argument of this call has a type that holds {pronoun}; a method call has "
            f"no '@(...)' slot, so write the type parameter in a parameter type")


def reject_unsolved_margs(validator: 'TypeValidator', method: str, unsolved, site,
                          help_text: str) -> None:
    """CE2063: the arguments of the call solve no method-level type parameter of `unsolved`."""
    names = ", ".join(f"'{n}'" for n in unsolved)
    er.emit_with(validator.reporter, er.ERR.CE2063, site,
                 plural="s" if len(unsolved) > 1 else "", names=names, method=method) \
        .help(help_text).emit()


def method_generic_copy(validator: 'TypeValidator', template, receiver_type, receiver_subst,
                        solved, site, report: bool):
    """The signature of the copy of a method-generic template at the solved method arguments.

    The receiver substitution and the method-parameter unification COMPOSE: the receiver
    names T, the arguments solve U. The signature is never added to the ExtensionTable,
    which keys on (type, name) alone. It holds the method arguments (the half of the symbol
    identity that a call reads), and the copy is queued for the analyzer's fixpoint round.
    A type argument that a rule refuses answers RESOLUTION_REPORTED, or None with no
    `report`.
    """
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    margs_names = list(template.method_type_param_names)
    margs = tuple(
        resolve_unknown_type(solved[n], validator.struct_table.by_name,
                             validator.enum_table.by_name)
        for n in margs_names)

    full_subst = dict(receiver_subst)
    full_subst.update(dict(zip(margs_names, margs, strict=True)))

    # E3 at the call, which is the instance: no copy is cut for a refused argument.
    if _refuses_non_error_arguments(validator, template, receiver_type, full_subst,
                                    site, report):
        return RESOLUTION_REPORTED if report else None

    receiver_names = [p.name if hasattr(p, "name") else p for p in template.type_params]
    receiver_args = tuple(receiver_subst[n] for n in receiver_names)
    if _refuses_unmet_bounds(validator, template, template.name, receiver_type,
                             receiver_args, margs, site, report=report):
        return RESOLUTION_REPORTED if report else None

    concrete = substituted_extension_signature(validator, template, receiver_type,
                                               full_subst)
    concrete.method_type_args = margs

    # A template check cuts no copy (#1070).
    if not validator.in_template_check:
        validator.extension_table.add_method_generic_copy(concrete)
        _queue_extension_instantiation(validator, template, receiver_type,
                                       receiver_args, margs, site)
    return concrete


def _refuses_unmet_bounds(validator: 'TypeValidator', template, method_name: str,
                          receiver_type, receiver_args, margs, site, *,
                          report: bool) -> bool:
    """CE4006 for a type argument of one call that breaks a bound, at the call.

    The TARGET bounds of the template (#1070) against the receiver's type arguments, and
    the method-level bounds (#1191) against the solved method arguments, in one call of
    the analyzer's `check_call_constraints` hook: the path of a free function. Each
    parameter that fails is reported, with the note at its bound. `report=False` asks
    the one predicate and emits nothing: an inference before the monomorphize stage
    asks too, and the hook does not exist yet.
    """
    method_params = (template.method_type_params
                     if isinstance(template, GenericExtensionMethod) else ())
    target_bounds = tuple(template.target_bounds)
    params = (*target_bounds, *method_params)
    if not any(tp.constraints for tp in params):
        return False
    args = (*receiver_args, *margs)
    if not report:
        return not target_bounds_hold(params, args, validator.tables.holds_bound)
    check = call_constraint_check(validator)
    key = ("method", _resolved(validator, receiver_type), method_name,
           tuple(_resolved(validator, arg) for arg in margs))
    return not check(params, args, key, site, validator.reporter.filename,
                     template.filename, report)


def _refuses_non_error_arguments(validator: 'TypeValidator', template, receiver_type,
                                 substitution, site, report: bool) -> bool:
    """CE2084 for a type argument of this call in an `E` position of the template (E3).

    docs/design/error-conversion.md section 2.5, item 2: the call that solves the
    arguments is the instance, and the note is at the template. One instance is
    reported one time, at its first call. `report=False` asks the same question and
    emits nothing.
    """
    from sushi_lang.semantics.error_types import non_error_type, reject_non_error_type
    from sushi_lang.semantics.generics.monomorphize.functions import (
        callable_error_parameters)

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    positions = callable_error_parameters(
        template.ret_type, template.err_type, template.err_span or template.ret_span,
        template.params, template.body, template.name_span, substitution)
    key = (_resolved(validator, receiver_type), template.name,
           tuple(_resolved(validator, substitution[name]) for name in sorted(positions)),
           template.unit_name)
    refused_keys = getattr(validator.tables, "refused_extension_keys", None)
    if refused_keys is not None and key in refused_keys:
        return True
    if not report:
        return any(non_error_type(substitution[name], structs, enums) is not None
                   for name in positions)
    refused = False
    for name, note_span in positions.items():
        note = (f"the template uses the type parameter '{name}' as an error type here",
                note_span, template.filename)
        if reject_non_error_type(validator.reporter, substitution[name], site,
                                 structs, enums, note=note):
            refused = True
    if refused and refused_keys is not None:
        refused_keys.add(key)
    return refused


def reject_uncloneable(validator: 'TypeValidator', call: 'ShirExpr', value_type) -> bool:
    """CE4018 or CE2431 when a value of `value_type` has no deep copy. True when refused.

    The one rule of `.clone()`, read by `.clone()` itself and by `clone_each`, which
    clones each lent item of an iterator (#1228).
    """
    from sushi_lang.semantics.typesys import holds_declared_resource

    def resolve(ty):
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        return resolve_unknown_type(ty, validator.struct_table.by_name,
                                    validator.enum_table.by_name)

    # One predicate (`holds_declared_resource`), and the type that answers it picks the
    # code: an opaque type parameter with no `Clone` (CE4018, #1070), or a handle.
    held: list = []
    if not holds_declared_resource(value_type, validator.drop_type_names,
                                   resolve=resolve, found=held):
        return False
    unpromised = [ty for ty in held if isinstance(ty, TypeParameter)]
    if unpromised:
        _reject_clone_of_opaque(validator, call, value_type, unpromised[0])
        return True

    er.emit_with(validator.reporter, er.ERR.CE2431, call.loc,
                 type=display_type(value_type)) \
        .help("a second owner of a handle is '.share()'; for a value that holds "
              "handles, build a new one from a '.share()' of each handle") \
        .emit()
    return True


def _reject_clone_of_opaque(validator: 'TypeValidator', call: 'ShirExpr',
                            receiver_type, param: TypeParameter) -> None:
    """CE4018: the receiver holds a type parameter that does not promise `Clone`.

    In a template check (#1070, R5) the parameter MAY hold a resource, so the body can
    clone a value that holds it only when the parameter promises `Clone`.
    """
    from sushi_lang.semantics.generics.opaque import explain_unpromised
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE4018, call.loc,
                              type=display_type(receiver_type), param=param.written())
    explain_unpromised(diagnostic, "Clone", [param]).emit()


def extension_call_result_type(validator: 'TypeValidator', method):
    """What a resolved method call YIELDS: the bare return, or its channel Result.

    `resolution.call_yield`, the rule every callee kind reads. A perk-implementation
    method spells its return `ret` and an ExtensionMethod spells it `ret_type`.
    """
    from ..resolution import call_yield
    declared = getattr(method, "ret_type", None)
    if declared is None:
        declared = getattr(method, "ret", None)
    err_type = getattr(method, "err_type", None)
    intern_signature(validator, declared, err_type)
    return call_yield(validator, declared, err_type)


def _unhandled_channel_payload(receiver_type):
    """The payload when the receiver is a `Result` or a `Maybe` instance, else None.

    It reads the type identity, never the variant names: a user enum with `Ok`/`Err`
    variants is not a `Result` (docs/design/error-conversion.md section 4).
    """
    from sushi_lang.semantics.generics.results import is_result_enum, result_ok_err
    from sushi_lang.semantics.type_predicates import is_instance_of

    if is_result_enum(receiver_type):
        return result_ok_err(receiver_type)[0]
    if isinstance(receiver_type, EnumType) and is_instance_of(receiver_type, "Maybe"):
        some_variant = receiver_type.get_variant("Some")
        if some_variant is not None and len(some_variant.associated_types) == 1:
            return some_variant.associated_types[0]
    return None


def reject_unhandled_channel_chain(validator: 'TypeValidator', method: str, receiver_type,
                                   loc, receiver_loc, called: Optional[str]) -> bool:
    """CE2515, the resolution FALLBACK of ruling 5.

    Fires only when resolution missed on a Result/Maybe receiver AND the method exists
    on the payload type -- which is what tells an unhandled channel from a typo. The
    diagnostic is relational: the primary names the missing method, the note points at
    the call that returned the wrapper (`receiver_loc`; `called` is its method name), and
    the help spells the fix: `??` for a Result, `.or_err(nom e)??` for a Maybe, because
    `??` takes a Result only.
    """
    from sushi_lang.semantics.type_predicates import is_instance_of

    payload = _unhandled_channel_payload(receiver_type)
    if payload is None or not _method_exists_on(validator, payload, method):
        return False

    diag = er.emit_with(validator.reporter, er.ERR.CE2515, loc,
                        method=method, wrapper=display_type(receiver_type))
    if receiver_loc is not None:
        diag.note_at("the unhandled channel comes from this call", receiver_loc)
    if is_instance_of(receiver_type, "Maybe"):
        fix = (f" -- e.g. '{called}().or_err(nom <error value>)??.{method}()'"
               if called else "")
        diag.help("handle the Maybe first: match on it, '.realise(default)', or give it "
                  f"an error value and propagate with '.or_err(nom e)??'{fix}").emit()
        return True
    fix = f" -- e.g. '{called}()??.{method}()'" if called else ""
    diag.help("handle the channel first: match on it, '.realise(default)', or "
              f"propagate with '??'{fix}").emit()
    return True


def reject_call_of_drop(validator: 'TypeValidator', loc: Span, receiver_type,
                        method: str) -> bool:
    """CE4020: a written call of the `drop` method of the predefined `Drop` perk (#1198).

    The compiler runs `drop()` at the scope exit, one time for each value, and the
    contract's `poke self` cannot end the receiver's life. The rule reads the PERK that
    gives the method, so a `drop` of a user perk stays an ordinary method.
    """
    from sushi_lang.semantics.passes.collect.perks import PerkCollector
    perk = validator.perk_impl_table.providing_perk(receiver_type, method)
    if perk != PerkCollector.DROP_PERK:
        return False
    er.emit_with(validator.reporter, er.ERR.CE4020, loc,
                 type=display_type(receiver_type)) \
        .help("to end the value early, let it leave its scope, give it to a function "
              "with 'nom', or declare a 'nom self' method on the type (as 'close()' "
              "on a stdlib handle)") \
        .emit()
    return True


def explain_unpromised_method(validator: 'TypeValidator', diag, receiver_type,
                               method_name: str) -> None:
    """The note and the help of CE2008 on an opaque type parameter (#1070).

    The body knows the methods its constraints promise and nothing more, so the fix is a
    constraint: the help names the perks that declare a method of this name.
    """
    from sushi_lang.semantics.generics.opaque import (
        bound_hint, note_opaque, providing_perks)
    if not (isinstance(receiver_type, TypeParameter) and receiver_type.is_opaque):
        return
    note_opaque(diag, receiver_type)
    perks = providing_perks(validator.perk_table, method_name)
    if perks:
        choices = " or ".join(bound_hint(receiver_type, perk) for perk in perks)
        diag.help(f"add a constraint that provides '{method_name}': {choices}")
    else:
        diag.help(f"no perk declares '{method_name}'; a type parameter has the methods "
                  f"of its constraints and nothing more")


def declaration_was_refused(validator: 'TypeValidator', receiver_type,
                            method_name: str) -> bool:
    """The collect pass refused this method's declaration, and said so there.

    An extension (#808) and a perk implementation (#860, #864) keep one record each,
    by the same key.
    """
    from sushi_lang.semantics.type_predicates import generic_base_of
    base = generic_base_of(receiver_type) or display_type(receiver_type)
    return any(record.was_refused(base, method_name)
               for record in (validator.generic_extension_table,
                              validator.tables.generic_perk_impls))


# The validation half of each built-in family, in the table's order. The claim is the
# registry's and is written once; what stands here is the CHECK that follows it.


def _method_exists_on(validator: 'TypeValidator', payload, method_name: str) -> bool:
    """Whether a method of this name would resolve on the payload type."""
    from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY

    if builtin_method_exists(payload, method_name, validator.derived_methods):
        return True
    if validator.extension_table.get_method(payload, method_name) is not None:
        return True
    if validator.perk_impl_table.get_method(payload, method_name) is not None:
        return True
    if isinstance(payload, DynamicArrayType):
        if validator.generic_extension_table.declarations(ARRAY_BASE_KEY, method_name):
            return True
    base_name = getattr(payload, "generic_base", None)
    if base_name is not None:
        if validator.generic_extension_table.find_applicable(
                base_name, method_name, payload.name) is not None:
            return True
    return False
