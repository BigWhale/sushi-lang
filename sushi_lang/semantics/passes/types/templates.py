"""The check of a function template, one time, where it is written (#1070).

A generic function body is checked with each type parameter OPAQUE: the body knows what
the constraints of the parameter promise, and nothing more. The check cuts a CHECK COPY
of the template in an overlay of the program tables (`semantics/template_scope.py`),
with each type parameter replaced by its opaque form, and the typecheck pass checks that
copy. Then the compiler discards the copy and the overlay. A fault of the template is
reported one time, at the template's spans, and the copies of a refused template report
nothing (the reporter mutes them).
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Dict, cast

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import BoundedTypeParam, Expand, FuncDef, Program
from sushi_lang.semantics.ast_walk import walk_nodes
from sushi_lang.semantics.generics.types import TemplateId

from . import TypeValidator

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef


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
            or _names_no_perk(validator, record)):
        return False

    reporter = validator.reporter
    reporter.leave_body()
    before = sum(reporter.errors_offered.values())
    _reject_second_homes(validator, record)

    from sushi_lang.semantics.generics.late_interning import settle_new_instances
    from sushi_lang.semantics.passes.finite_types import table_marks
    from sushi_lang.semantics.passes.lift import LambdaLifter
    from sushi_lang.semantics.template_scope import template_scope

    scope = template_scope(validator.tables)
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

    checker = TemplateValidator(
        reporter, scope.tables, current_unit_name=validator.current_unit_name,
        monomorphized_functions=validator.monomorphized_functions,
        in_library_unit=validator.in_library_unit, namespaces=validator.namespaces)
    checker._validate_function(check_copy)
    LambdaLifter(scope.tables.structs, scope.funcs, _scratch_program(),
                 annotate=checker).lift_function(check_copy)
    reporter.leave_body()

    if sum(reporter.errors_offered.values()) > before:
        validator.tables.refused_templates.add(TemplateId(record.unit_name, record.name))
    return True


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


def _names_no_perk(validator: TypeValidator, record: 'GenericFuncDef') -> bool:
    """A constraint names no perk: the constraint check refused it, and that is the one
    fault. The body is not checked against a promise that does not exist."""
    return any(validator.perk_table.get(name) is None
               for tp in record.type_params for name in (tp.constraints or ()))


def _reject_second_homes(validator: TypeValidator, record: 'GenericFuncDef') -> None:
    """CE4015 for a method that two constraints of one type parameter both declare.

    A name has one home on a type, and an opaque parameter has the methods of its
    constraints. The first constraint answers the body check, so the check stays total.
    """
    perks = validator.perk_table
    for tp in record.type_params:
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
