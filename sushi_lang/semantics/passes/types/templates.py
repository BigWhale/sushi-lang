"""The check of a template, one time, where it is written (#1070).

A generic function body, an extension template and a perk template are checked with each
type parameter OPAQUE: the body knows what the constraints of the parameter promise,
and nothing more. A receiver parameter of an extension or a perk template has the
bounds its base declares and the bounds its target adds (R1). The check cuts a CHECK COPY
of the template in an overlay of the program tables (`semantics/template_scope.py`),
with each type parameter replaced by its opaque form, and the typecheck pass checks that
copy. Then the compiler discards the copy and the overlay. A fault of the template is
reported one time, at the template's spans, and the copies of a refused template report
nothing (the reporter mutes them).
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Sequence, cast

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import BoundedTypeParam, Expand, ExtendDef, FuncDef, Program
from sushi_lang.semantics.ast_walk import walk_nodes
from sushi_lang.semantics.generics.types import TemplateId
from sushi_lang.semantics.typesys import DynamicArrayType

from . import TypeValidator

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.template_scope import TemplateScope


class TemplateValidator(TypeValidator):
    """The typecheck pass over a check copy: it reads, and it cuts no copy.

    A check copy names instances over an opaque parameter, and none of them may reach a
    program table or a queue. Every writer that cuts or queues a copy asks
    `in_template_check` (or one of the two flags) and answers the substituted signature
    alone.
    """

    queues_late_copies = False
    requests_late_instances = False
    in_template_check = True


def check_function_template(validator: TypeValidator, func: FuncDef) -> bool:
    """Check one function template on a check copy. Answers whether it was checked.

    A template with a pack parameter, or a body with an `expand`, is not checked here
    (packs are a later phase), and neither is a refused redeclaration, whose record is
    another body.
    """
    record = validator.generic_func_table.lookup(func.name, validator.current_unit_name)
    if (record is None or record.body is not func.body or not _checkable(record)
            or _names_no_perk(validator, record.type_params)):
        return False

    check = _open(validator)
    _reject_second_homes(validator, record.type_params)
    from sushi_lang.semantics.generics.late_interning import settle_new_instances
    from sushi_lang.semantics.passes.finite_types import table_marks
    scope = check.scope
    marks = table_marks(scope.tables.structs, scope.tables.enums)
    # The deep copy comes FIRST: the cut shares every node it does not visit with its
    # source, and a stamp on the template would reach every later copy of it.
    check_copy = scope.monomorphizer.function_monomorphizer.cut_body(
        copy.deepcopy(record), cast(Dict[str, Any], record.opaque))
    # The fields a copy carries, as the monomorphize stage sets them. The check copy is
    # no copy of a template for the reporter and the statement rules: it IS the template,
    # under its own name, so CE0107 and every other message names it as written.
    check_copy.name = func.name
    check_copy.instance_of = None
    check_copy.pack_names = ()
    check_copy.is_synthesized = False
    settle_new_instances(scope.tables, scope.reporter, marks)

    checker = _checker(check)
    checker._validate_function(check_copy)
    _lifter(check, checker).lift_function(check_copy)
    _close(check, TemplateId(record.unit_name, record.name))
    return True


def check_extension_template(validator: TypeValidator, ext: ExtendDef) -> bool:
    """Check one extension template on a check copy. Answers whether it was checked.

    A generic target, an array target and a method-level type parameter on any target
    (#1070). Each receiver parameter is opaque with its receiver bounds (R1: what the
    base declares, and what the target adds), and each method-level parameter with its
    own constraints, as parameters of ONE owner. A refused declaration, and a concrete
    target with no method-level parameter, is no template.
    """
    from sushi_lang.semantics.generics.extension_targets import (
        ARRAY_BASE_KEY, extension_template_id, receiver_bounds)
    from sushi_lang.semantics.passes.collect.functions import opaque_type_params

    record = validator.generic_extension_table.record_of(ext)
    if record is None or not (record.type_params or record.method_type_params):
        return False
    receiver = receiver_bounds(record, validator.generic_struct_table,
                               validator.generic_enum_table)
    params = (*receiver, *record.method_type_params)
    if _names_no_perk(validator, params):
        return False

    check = _open(validator)
    _reject_second_homes(validator, params)
    template_id = extension_template_id(record)
    opaque = opaque_type_params(params, record.unit_name, template_id.name)
    receiver_args = tuple(opaque[bound.name] for bound in receiver)
    method_args = tuple(opaque[name] for name in record.method_type_param_names)
    target: Any
    if record.base_type_name == ARRAY_BASE_KEY:
        target = DynamicArrayType(base_type=receiver_args[0])
    elif record.type_params:
        target = _overlay_instance(check.scope, record.base_type_name, receiver_args)
    else:
        target = ext.target_type

    from sushi_lang.semantics.generics.extensions import monomorphize_extension_method
    from sushi_lang.semantics.generics.late_interning import (
        intern_copy_signatures, settle_new_instances)
    from sushi_lang.semantics.passes.finite_types import table_marks
    scope = check.scope
    marks = table_marks(scope.tables.structs, scope.tables.enums)
    # The deep copy comes FIRST, for the reason of a function template.
    check_copy = monomorphize_extension_method(
        dataclasses.replace(record, decl=copy.deepcopy(record.decl)), target,
        receiver_args, scope.monomorphizer.substitutor, method_args)
    # The check copy IS the template: written, in the unit that declares it, and no copy
    # of a template for the mute. Its target is then validated as a written type, which
    # is the R1 check of the target against the bounds of its base.
    check_copy.home_unit = None
    check_copy.scope_unit = None
    check_copy.template_id = None
    intern_copy_signatures(scope.tables, scope.monomorphizer, [check_copy])
    settle_new_instances(scope.tables, scope.reporter, marks)

    checker = _checker(check)
    checker._validate_extension_method(check_copy)
    _lifter(check, checker).lift_body(check_copy.body)
    _close(check, template_id)
    return True


def _overlay_instance(scope: 'TemplateScope', base: str, args: tuple):
    """The instance `base@(args)` over opaque parameters, interned in the overlay."""
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.type_resolution import resolve_type_recursively
    ref = GenericTypeRef(base_name=base, type_args=args)
    scope.intern(ref)
    return resolve_type_recursively(ref, scope.tables.structs.by_name,
                                    scope.tables.enums.by_name)


@dataclass
class _Check:
    """One template check: the validator that asked, its overlay, the error count before."""

    validator: TypeValidator
    scope: 'TemplateScope'
    before: int


def _open(validator: TypeValidator) -> _Check:
    """Start a check: no body is entered, the error count is read, an overlay is made."""
    from sushi_lang.semantics.template_scope import template_scope
    reporter = validator.reporter
    reporter.leave_body()
    return _Check(validator, template_scope(validator.tables),
                  sum(reporter.errors_offered.values()))


def _checker(check: _Check) -> 'TemplateValidator':
    """The typecheck pass over the check copy: the one construction of it."""
    validator = check.validator
    return TemplateValidator(
        validator.reporter, check.scope.tables, current_unit_name=validator.current_unit_name,
        monomorphized_functions=validator.monomorphized_functions,
        in_library_unit=validator.in_library_unit, namespaces=validator.namespaces)


def _lifter(check: _Check, checker: 'TemplateValidator'):
    """The lift pass over the check copy: its lambdas land in a scratch program."""
    from sushi_lang.semantics.passes.lift import LambdaLifter
    return LambdaLifter(check.scope.tables.structs, check.scope.funcs, _scratch_program(),
                        annotate=checker)


def _close(check: _Check, template_id: TemplateId) -> None:
    """End a check: a template whose check reported an error is refused, and its copies
    report nothing (the reporter mutes them)."""
    reporter = check.validator.reporter
    reporter.leave_body()
    if sum(reporter.errors_offered.values()) > check.before:
        check.validator.tables.refused_templates.add(template_id)


def _checkable(record: 'GenericFuncDef') -> bool:
    """The template check covers it: every type parameter has its opaque form, and the
    body holds no `expand`."""
    names = [getattr(tp, "name", tp) for tp in record.type_params]
    if any(getattr(tp, "is_pack", False) for tp in record.type_params):
        return False
    if set(names) != set(record.opaque):
        return False
    found = []

    def visit(node) -> bool:
        if isinstance(node, Expand):
            found.append(node)
        return not found

    walk_nodes(record.body, visit)
    return not found


def _names_no_perk(validator: TypeValidator, params: Sequence[Any]) -> bool:
    """A constraint names no perk: the constraint check refused it, and that is the one
    fault. The body is not checked against a promise that does not exist."""
    return any(validator.perk_table.get(name) is None
               for tp in params for name in (tp.constraints or ()))


def _reject_second_homes(validator: TypeValidator, params: Sequence[Any]) -> None:
    """CE4015 for a method that two constraints of one type parameter both declare.

    A name has one home on a type, and an opaque parameter has the methods of its
    constraints. The first constraint answers the body check, so the check stays total.
    The constraints of a receiver parameter are its implied bounds and its target bounds
    together (#1070, R1).
    """
    perks = validator.perk_table
    for tp in params:
        if not isinstance(tp, BoundedTypeParam):
            continue
        seen: dict[str, str] = {}
        for index, perk_name in enumerate(tp.constraints or ()):
            perk = perks.get(perk_name)
            if perk is None:
                continue
            for method in perk.methods:
                first = seen.setdefault(method.name, perk_name)
                if first == perk_name:
                    continue
                diagnostic = er.emit_with(
                    validator.reporter, er.ERR.CE4015,
                    tp.constraint_span(index) or tp.loc, perk=perk_name,
                    method=method.name, other=first)
                first_span = tp.constraint_span(tp.constraints.index(first))
                if first_span is not None:
                    diagnostic = diagnostic.note_at(
                        f"'{first}' provides '{method.name}' here", first_span)
                diagnostic.emit()


def _scratch_program() -> Program:
    """A program that holds nothing: the lifted lambdas of a check copy land here."""
    return Program(loc=None, uses=[], constants=[], structs=[], enums=[], perks=[],
                   functions=[], extensions=[], generic_extensions=[], perk_impls=[])
