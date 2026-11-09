"""The check of a template, one time, where it is written (#1070).

A generic function body, an extension template and a perk template are checked with each
type parameter OPAQUE: the body knows what the constraints of the parameter promise,
and nothing more. A receiver parameter of an extension or a perk template has the
bounds its base declares and the bounds its target adds (R1). The check makes a HEADER of
the template in an overlay of the program tables (`semantics/template_scope.py`), with each
type parameter replaced by its opaque form and with no body. The typecheck pass checks that
header, and it walks the SHIR body of the template as written, whose origin is
`TemplateCheck` (docs/design/ir.md 4.3). A body that the typecheck pass finished with no
error goes on to the lift pass and the borrow pass, in the overlay, as every body does.
Then the compiler discards the header and the overlay. A fault of the template is reported
one time, at the template's spans, and the copies of a refused template report nothing
(the reporter mutes them).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Sequence, TypeVar, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Block, BoundedTypeParam, ExtendDef, ExtendWithDef, FuncDef
from sushi_lang.semantics.generics.types import TemplateId, TypeParameter
from sushi_lang.semantics.passes.collect.perks import reject_second_homes
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.typesys import DynamicArrayType

from . import TypeValidator

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
    from sushi_lang.semantics.template_scope import TemplateScope


_Decl = TypeVar("_Decl")


class PackElements:
    """The element type of each `expand` of one template check (#1070, R6).

    One element type per `expand` node of the SHIR body, numbered in the order the check
    meets them. The number only has to be unique in one template check: the overlay and
    every instance that names an element type are discarded after it.
    """

    def __init__(self, record: 'GenericFuncDef', unit: Optional[str]) -> None:
        packs = [tp for tp in record.type_params
                 if isinstance(tp, BoundedTypeParam) and tp.is_pack]
        self._pack = packs[-1] if packs else None
        self._unit = unit
        self._template = record.name
        self._memo: Dict[ShirId, TypeParameter] = {}

    def element_of(self, node: ShirId, binder: str, binder_span: Optional[Span]) -> TypeParameter:
        found = self._memo.get(node)
        if found is not None:
            return found
        if self._pack is None:
            er.raise_internal_error(
                "CE0015", message=f"an `expand` in '{self._template}', which has no pack")
        from sushi_lang.semantics.passes.collect.functions import opaque_pack_element
        element = opaque_pack_element(cast(BoundedTypeParam, self._pack), self._unit,
                                      self._template, len(self._memo), binder, binder_span)
        self._memo[node] = element
        return element


class TemplateValidator(TypeValidator):
    """The typecheck pass over a template check: it reads, and it cuts no copy.

    A check names instances over an opaque parameter, and none of them may reach a
    program table or a queue. Every writer that cuts or queues a copy asks
    `in_template_check` (or the flag of the queue) and answers the substituted signature
    alone.
    """

    queues_late_copies = False
    in_template_check = True

    def __init__(self, *args: Any, scope: 'TemplateScope', opaque: Mapping[str, Any],
                 pack_elements: Optional[PackElements] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pack_elements = pack_elements
        # The header of the check -> the template as written, whose SHIR body is the body
        # of the check.
        self.written: Dict[int, Any] = {}
        # The overlay of the check, and the opaque form of each type parameter by name.
        self._scope = scope
        self._opaque = dict(opaque)

    def written_of(self, decl: Any) -> Any:
        return self.written.get(id(decl), decl)

    def written_type(self, ty: Any) -> Any:
        """A type of the template as written, at the opaque type parameters (ir.md 4.3)."""
        return self._scope.monomorphizer.substitutor.substitute_type(ty, self._opaque)


def check_function_template(validator: TypeValidator, func: FuncDef) -> bool:
    """Check one function template. Answers whether it was checked.

    A refused redeclaration is not checked here, because its record is another body.
    The header of a pack template holds no value pack: each `expand` binds an element type
    (`PackElements`), and a use of the pack name elsewhere is CE0144.
    """
    record = validator.generic_func_table.lookup(func.name, validator.current_unit_name)
    if (record is None or record.body is not func.body or not _checkable(record)
            or _names_no_perk(validator, record.type_params)):
        return False

    check = _open(validator)
    reject_second_homes(validator.reporter, validator.perk_table, record.type_params)
    scope = check.scope

    marks = _table_marks(scope)
    header = scope.monomorphizer.function_monomorphizer.cut_body(
        _with_no_body(record), cast(Dict[str, Any], record.opaque))
    # The fields a copy carries, as the monomorphize stage sets them. The header is no
    # copy of a template for the reporter and the statement rules: it IS the template,
    # under its own name, so CE0107 and every other message names it.
    header.name = func.name
    header.instance_of = None
    # The value pack is not a value: no parameter holds it.
    header.params = [p for p in header.params if not p.is_pack]
    header.is_synthesized = False
    _settle(scope, marks, ())
    elements = PackElements(record, validator.current_unit_name)
    checker = _checker(check, record.opaque, elements)
    checker.written[id(header)] = func
    checker._validate_function(header)
    _close(check, TemplateId(record.unit_name, record.name))
    return True


def check_extension_template(validator: TypeValidator, ext: ExtendDef) -> bool:
    """Check one extension template. Answers whether it was checked.

    A generic target, an array target and a method-level type parameter on any target
    (#1070). Each receiver parameter is opaque with its receiver bounds (R1: what the
    base declares, and what the target adds), and each method-level parameter with its
    own constraints, as parameters of ONE owner. A refused declaration, and a concrete
    target with no method-level parameter, is no template. A method-level type pack has
    no opaque form: a method has no value pack, so no `expand` gives it an element type,
    and the template is not checked.
    """
    from sushi_lang.semantics.generics.extension_targets import extension_template_id
    from sushi_lang.semantics.generics.extensions import monomorphize_extension_method

    record = validator.generic_extension_table.record_of(ext)
    if record is None or not (record.type_params or record.method_type_params):
        return False
    if any(tp.is_pack for tp in record.method_type_params):
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

    marks = _table_marks(scope)
    header = monomorphize_extension_method(
        dataclasses.replace(record, decl=_with_no_body(record.decl)), target, receiver_args,
        scope.monomorphizer.substitutor, method_args)
    # The header IS the template: written, in the unit that declares it, and no copy of a
    # template for the mute. Its target is then validated as a written type, which is the
    # R1 check of the target against the bounds of its base.
    header.home_unit = None
    header.scope_unit = None
    header.template_id = None
    _settle(scope, marks, [header])
    checker = _checker(check, opaque)
    checker.written[id(header)] = ext
    checker._validate_extension_method(header)
    _close(check, template_id)
    return True


def check_perk_template(validator: TypeValidator, impl: ExtendWithDef) -> bool:
    """Check one perk template. Answers whether it was checked.

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
    check, opaque, args = opened
    scope = check.scope
    target = _receiver_instance(scope, template.base_type_name, args)

    marks = _table_marks(scope)
    header = monomorphize_perk_impl(
        dataclasses.replace(template, impl=dataclasses.replace(
            template.impl, methods=[_with_no_body(method) for method in template.impl.methods])),
        target, args, scope.monomorphizer.substitutor)
    # The header IS the template: written, and no copy of a template for the mute.
    header.is_synthesized = False
    for method in header.methods:
        method.instance_of = None
        method.template_id = None
    _settle(scope, marks, header.methods)
    checker = _checker(check, opaque)
    checker.written.update((id(copied), written) for copied, written
                           in zip(header.methods, impl.methods, strict=True))
    from .signatures import validate_perk_template_bodies
    validate_perk_template_bodies(checker, target, header)
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
    reject_second_homes(validator.reporter, validator.perk_table, params,
                        template.base_type_name)
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


def _with_no_body(decl: _Decl) -> _Decl:
    """`decl` with an empty body: a check makes a header of it, and walks the SHIR body."""
    return dataclasses.replace(decl, body=Block(None, []))  # type: ignore[type-var]


def _table_marks(scope: 'TemplateScope') -> Any:
    """The marks of the tables of the overlay, just before a header is made, after the
    receiver instance is interned, so no instance is settled twice."""
    from sushi_lang.semantics.passes.finite_types import table_marks
    return table_marks(scope.tables.structs, scope.tables.enums)


def _settle(scope: 'TemplateScope', marks: Any, callables: Sequence[Any]) -> None:
    """Intern what the signatures of `callables` name, and settle the new instances."""
    from sushi_lang.semantics.generics.late_interning import (
        intern_copy_signatures, settle_new_instances)
    intern_copy_signatures(scope.tables, scope.monomorphizer, callables)
    settle_new_instances(scope.tables, scope.reporter, marks)


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


def _checker(check: _Check, opaque: Mapping[str, Any],
             pack_elements: Optional[PackElements] = None) -> 'TemplateValidator':
    """The typecheck pass over a template check: the one construction of it. `opaque` is the
    opaque form of each type parameter. A pack template gives the element type of each
    `expand`."""
    validator = check.validator
    return TemplateValidator(
        validator.reporter, check.scope.tables, current_unit_name=validator.current_unit_name,
        monomorphized_functions=validator.monomorphized_functions,
        in_library_unit=validator.in_library_unit, namespaces=validator.namespaces,
        shir=validator.shir, next_stage=validator.next_stage,
        program_failed=validator.program_failed,
        scope=check.scope, opaque=opaque, pack_elements=pack_elements)


def _close(check: _Check, template_id: TemplateId) -> None:
    """End a check: a template whose check reported an error is refused, and its copies
    report nothing (the reporter mutes them). A copy of a template that checked clean
    reports only the per-instance remainder (R7, R8)."""
    reporter = check.validator.reporter
    reporter.leave_body()
    check.validator.tables.checked_templates.add(template_id)
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
