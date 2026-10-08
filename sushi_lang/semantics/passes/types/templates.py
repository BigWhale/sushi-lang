"""The check of a template, one time, where it is written (#1070).

A generic function body, an extension template and a perk template are checked with each
type parameter OPAQUE: the body knows what the constraints of the parameter promise,
and nothing more. A receiver parameter of an extension or a perk template has the
bounds its base declares and the bounds its target adds (R1). The check cuts a CHECK COPY
of the template in an overlay of the program tables (`semantics/template_scope.py`),
with each type parameter replaced by its opaque form. The typecheck pass, the lift pass
and the borrow pass check that copy (R5: an opaque parameter moves). Then the compiler discards the copy and the overlay. A fault of the template is
reported one time, at the template's spans, and the copies of a refused template report
nothing (the reporter mutes them).
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Sequence, TypeVar, cast

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    BoundedTypeParam, Expand, ExtendDef, ExtendWithDef, FuncDef, Program)
from sushi_lang.semantics.generics.types import TemplateId, TypeParameter
from sushi_lang.semantics.typesys import DynamicArrayType

from . import TypeValidator

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.template_scope import TemplateScope


class PackElements:
    """The element type of each `expand` of one check copy (#1070, R6).

    One element type per `expand` node, numbered in the order the check meets them.
    The number only has to be unique in one template check: the check copy, the overlay
    and every instance that names an element type are discarded after it.
    """

    def __init__(self, record: 'GenericFuncDef', unit: Optional[str]) -> None:
        packs = [tp for tp in record.type_params
                 if isinstance(tp, BoundedTypeParam) and tp.is_pack]
        self._pack = packs[-1] if packs else None
        self._unit = unit
        self._template = record.name
        self._memo: Dict[int, TypeParameter] = {}

    def element_of(self, node: Expand) -> TypeParameter:
        found = self._memo.get(id(node))
        if found is not None:
            return found
        if self._pack is None:
            er.raise_internal_error(
                "CE0015", message=f"an `expand` in '{self._template}', which has no pack")
        from sushi_lang.semantics.passes.collect.functions import opaque_pack_element
        element = opaque_pack_element(cast(BoundedTypeParam, self._pack), self._unit,
                                      self._template, len(self._memo), node.var,
                                      node.var_span)
        self._memo[id(node)] = element
        return element


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

    def __init__(self, *args: Any, pack_elements: Optional[PackElements] = None,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pack_elements = pack_elements


def check_function_template(validator: TypeValidator, func: FuncDef) -> bool:
    """Check one function template on a check copy. Answers whether it was checked.

    A refused redeclaration is not checked here, because its record is another body.
    The check copy of a pack template holds no value pack: each `expand` binds an
    element type (`PackElements`), and a use of the pack name elsewhere is CE0144.
    """
    record = validator.generic_func_table.lookup(func.name, validator.current_unit_name)
    if (record is None or record.body is not func.body or not _checkable(record)
            or _names_no_perk(validator, record.type_params)):
        return False

    check = _open(validator)
    _reject_second_homes(validator, record.type_params)
    scope = check.scope

    def cut() -> FuncDef:
        # The deep copy comes FIRST: the cut shares every node it does not visit with
        # its source, and a stamp on the template would reach every later copy of it.
        check_copy = scope.monomorphizer.function_monomorphizer.cut_body(
            copy.deepcopy(record), cast(Dict[str, Any], record.opaque))
        # The fields a copy carries, as the monomorphize stage sets them. The check copy
        # is no copy of a template for the reporter and the statement rules: it IS the
        # template, under its own name, so CE0107 and every other message names it.
        check_copy.name = func.name
        check_copy.instance_of = None
        # The value pack is not a value in the check copy: no parameter holds it, and
        # its name stays in `pack_names`, so a use of it is CE0144.
        check_copy.params = [p for p in check_copy.params if not p.is_pack]
        check_copy.pack_names = tuple(p.name for p in record.params if p.is_pack)
        check_copy.is_synthesized = False
        return check_copy

    # The cut interns what the copy names itself.
    check_copy = _cut_and_settle(scope, cut, lambda _copy: ())
    elements = PackElements(record, validator.current_unit_name)
    checker = _checker(check, elements)
    checker._validate_function(check_copy)
    lifted = _lifter(check, checker).lift_function(check_copy)
    _borrow(check, check_copy, lifted, elements,
            frozenset(p.name for p in record.params if p.is_pack and p.is_nom))
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
    from sushi_lang.semantics.generics.extension_targets import extension_template_id
    from sushi_lang.semantics.generics.extensions import monomorphize_extension_method

    record = validator.generic_extension_table.record_of(ext)
    if record is None or not (record.type_params or record.method_type_params):
        return False
    template_id = extension_template_id(record)
    opened = _open_receiver(validator, record, template_id, record.method_type_params)
    if opened is None:
        return False
    check, opaque, receiver_args = opened
    scope = check.scope
    method_args = tuple(opaque[name] for name in record.method_type_param_names)
    # A concrete receiver (`extend i32 pick@(U)`) is the written target itself.
    target: Any = (_receiver_instance(scope, record.base_type_name, receiver_args)
                   if record.type_params else ext.target_type)

    def cut() -> ExtendDef:
        # The deep copy comes FIRST, for the reason of a function template.
        check_copy = monomorphize_extension_method(
            dataclasses.replace(record, decl=copy.deepcopy(record.decl)), target,
            receiver_args, scope.monomorphizer.substitutor, method_args)
        # The check copy IS the template: written, in the unit that declares it, and no
        # copy of a template for the mute. Its target is then validated as a written
        # type, which is the R1 check of the target against the bounds of its base.
        check_copy.home_unit = None
        check_copy.scope_unit = None
        check_copy.template_id = None
        return check_copy

    check_copy = _cut_and_settle(scope, cut, lambda ext_copy: [ext_copy])
    checker = _checker(check)
    checker._validate_extension_method(check_copy)
    lifted = _lifter(check, checker).lift_body(check_copy.body)
    _borrow(check, check_copy, lifted)
    _close(check, template_id)
    return True


def check_perk_template(validator: TypeValidator, impl: ExtendWithDef) -> bool:
    """Check one perk template on a check copy. Answers whether it was checked.

    `extend Box@(T) with P:` and `extend T[] with P:` (#1070). Each parameter of the
    target is opaque with its receiver bounds (R1). The header was judged on the written
    template (`validate_template_header`); the check reads the method bodies. A refused
    implementation, and one of a perk that no unit declares, is not checked.
    """
    from sushi_lang.semantics.generics.extension_targets import perk_template_id
    from sushi_lang.semantics.generics.extensions import monomorphize_perk_impl

    template = validator.tables.generic_perk_impls.record_of(impl)
    if template is None or validator.perk_table.get(impl.perk_name) is None:
        return False
    template_id = perk_template_id(template)
    opened = _open_receiver(validator, template, template_id)
    if opened is None:
        return False
    check, _opaque, args = opened
    scope = check.scope
    target = _receiver_instance(scope, template.base_type_name, args)

    def cut() -> ExtendWithDef:
        # The deep copy comes FIRST, for the reason of a function template.
        check_copy = monomorphize_perk_impl(
            dataclasses.replace(template, impl=copy.deepcopy(template.impl)), target,
            args, scope.monomorphizer.substitutor)
        # The check copy IS the template: written, and no copy of a template for the mute.
        check_copy.is_synthesized = False
        for method in check_copy.methods:
            method.instance_of = None
            method.template_id = None
        return check_copy

    check_copy = _cut_and_settle(scope, cut, lambda impl_copy: impl_copy.methods)
    checker = _checker(check)
    from .signatures import validate_perk_template_bodies
    validate_perk_template_bodies(checker, target, check_copy)
    lifted = _lifter(check, checker).lift_perk_impl(check_copy)
    _borrow(check, check_copy, lifted)
    _close(check, template_id)
    return True


def _open_receiver(validator: TypeValidator, template, template_id: TemplateId,
                   method_params: Sequence[BoundedTypeParam] = ()):
    """Open the check of an extension or a perk template: (check, opaque, receiver args).

    Each receiver parameter gets its receiver bounds (R1) and each method-level one its
    own constraints, as opaque parameters of ONE owner. None when a bound names no perk:
    CE4003 is that one fault.
    """
    from sushi_lang.semantics.generics.extension_targets import (
        receiver_bounds, target_bound_forms)
    from sushi_lang.semantics.passes.collect.functions import opaque_type_params

    receiver = receiver_bounds(template, validator.generic_struct_table,
                               validator.generic_enum_table)
    params = (*receiver, *method_params)
    if _names_no_perk(validator, params):
        return None
    check = _open(validator)
    _reject_second_homes(validator, params, template.base_type_name)
    opaque = opaque_type_params(params, template.unit_name, template_id.name,
                                target_bound_forms(template.base_type_name, receiver))
    return check, opaque, tuple(opaque[bound.name] for bound in receiver)


def _receiver_instance(scope: 'TemplateScope', base: str, args: tuple):
    """The receiver over opaque parameters: `T[]`, or `base@(args)` interned in the overlay."""
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.type_resolution import resolve_type_recursively
    if base == ARRAY_BASE_KEY:
        return DynamicArrayType(base_type=args[0])
    ref = GenericTypeRef(base_name=base, type_args=args)
    scope.intern(ref)
    return resolve_type_recursively(ref, scope.tables.structs.by_name,
                                    scope.tables.enums.by_name)


_Copy = TypeVar("_Copy")


def _cut_and_settle(scope: 'TemplateScope', cut: Callable[[], _Copy],
                    callables: Callable[[_Copy], Sequence[Any]]) -> _Copy:
    """Cut the check copy, intern what its `callables` name, and settle the new instances.

    The marks are taken just before the cut, after the receiver instance is interned, so
    no instance is settled twice.
    """
    from sushi_lang.semantics.generics.late_interning import (
        intern_copy_signatures, settle_new_instances)
    from sushi_lang.semantics.passes.finite_types import table_marks
    marks = table_marks(scope.tables.structs, scope.tables.enums)
    check_copy = cut()
    intern_copy_signatures(scope.tables, scope.monomorphizer, callables(check_copy))
    settle_new_instances(scope.tables, scope.reporter, marks)
    return check_copy


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


def _checker(check: _Check,
             pack_elements: Optional[PackElements] = None) -> 'TemplateValidator':
    """The typecheck pass over the check copy: the one construction of it. A pack
    template gives the element type of each `expand`."""
    validator = check.validator
    return TemplateValidator(
        validator.reporter, check.scope.tables, current_unit_name=validator.current_unit_name,
        monomorphized_functions=validator.monomorphized_functions,
        in_library_unit=validator.in_library_unit, namespaces=validator.namespaces,
        pack_elements=pack_elements)


def _lifter(check: _Check, checker: 'TemplateValidator'):
    """The lift pass over the check copy: its lambdas land in a scratch program."""
    from sushi_lang.semantics.passes.lift import LambdaLifter
    return LambdaLifter(check.scope.tables.structs, check.scope.funcs, _scratch_program(),
                        annotate=checker)


def _borrow(check: _Check, check_copy: Any, lifted: Sequence[FuncDef],
            elements: Optional[PackElements] = None,
            owned_packs: frozenset = frozenset()) -> None:
    """The borrow pass over the check copy and its lifted lambdas (#1070, R5).

    It runs before `_close`, so a borrow fault refuses the template and mutes every copy
    of it. The analyzer gives the hook; with no hook the template has no borrow check.
    """
    from sushi_lang.semantics.template_scope import CheckCopy
    hook = check.validator.borrow_check_copy
    if hook is None:
        er.raise_internal_error(
            "CE0015", message="the analyzer set no borrow check for a check copy")
        return
    hook(CheckCopy(check_copy, tuple(lifted), check.scope.tables,
                   elements.element_of if elements is not None else None, owned_packs))


def _close(check: _Check, template_id: TemplateId) -> None:
    """End a check: a template whose check reported an error is refused, and its copies
    report nothing (the reporter mutes them)."""
    reporter = check.validator.reporter
    reporter.leave_body()
    if sum(reporter.errors_offered.values()) > check.before:
        check.validator.tables.refused_templates.add(template_id)


def _checkable(record: 'GenericFuncDef') -> bool:
    """The template check covers it: every type parameter but the pack has its opaque
    form."""
    names = {getattr(tp, "name", tp) for tp in record.type_params
             if not getattr(tp, "is_pack", False)}
    return names == set(record.opaque)


def _names_no_perk(validator: TypeValidator, params: Sequence[Any]) -> bool:
    """A constraint names no perk: the constraint check refused it, and that is the one
    fault. The body is not checked against a promise that does not exist."""
    return any(validator.perk_table.get(name) is None
               for tp in params for name in (tp.constraints or ()))


def _reject_second_homes(validator: TypeValidator, params: Sequence[Any],
                         base: Optional[str] = None) -> None:
    """CE4015 for a method that two constraints of one type parameter both declare.

    A name has one home on a type, and an opaque parameter has the methods of its
    constraints. The first constraint answers the body check, so the check stays total.
    The constraints of a receiver parameter are its implied bounds and its target bounds
    together (#1070, R1): `base` is the target's base, and an implied bound is noted in
    the file that declares it, or in prose when no line states it (the HashMap key rule).
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
                    tp.constraint_span(index) or tp.loc,
                    filename=tp.constraint_file(index), perk=perk_name,
                    method=method.name, other=first)
                at = tp.constraints.index(first)
                first_span = tp.constraint_span(at)
                if first_span is not None:
                    diagnostic = diagnostic.note_at(
                        f"'{first}' provides '{method.name}' here", first_span,
                        tp.constraint_file(at))
                elif base is not None:
                    from sushi_lang.semantics.generics.extension_targets import (
                        implied_bound_note)
                    diagnostic = diagnostic.note(implied_bound_note(base, tp.name, first))
                diagnostic.emit()


def _scratch_program() -> Program:
    """A program that holds nothing: the lifted lambdas of a check copy land here."""
    return Program(loc=None, uses=[], constants=[], structs=[], enums=[], perks=[],
                   functions=[], extensions=[], generic_extensions=[], perk_impls=[])
