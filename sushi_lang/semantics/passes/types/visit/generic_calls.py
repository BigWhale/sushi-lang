"""The call of a generic function in the typecheck pass over SHIR (docs/design/ir.md 7.3, 8.2).

A call names a template, bare or behind an alias. Its type arguments are written, or the
arguments solve them through the one leading solver, and the pass keeps the answer of each
argument. The elements of a pack are the arguments from the pack parameter on.

An instance is one body: the arm looks up its signature in the declaring unit, or requests
the late copy (S11, S19). The callee row names the instance, with the type arguments and the
declaring unit, and the rest of the call is the call of a concrete function. A check of a
template cuts no instance: the arguments are measured against the signature that the type
parameters of the template go through, and the callee row names the template.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.explicit_type_args import (
    check_explicit_type_arg_arity, resolve_explicit_type_args)
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.opaque import note_opaque
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.unify import as_type_argument
from sushi_lang.semantics.passes.types.arguments import (
    NOTHING_TYPED, check_arguments, reject_misplaced_spread)
from sushi_lang.semantics.passes.types.calls.generics import (
    constraints_hold_in_template, substituted_sig)
from sushi_lang.semantics.generics.types import substitute_type_params
from sushi_lang.semantics.passes.types.visibility import name_is_contested, reject_private_call
from sushi_lang.semantics.passes.types.visit.constructions import (
    TypedArguments, is_bare_lambda, is_generic_value, names_a_parameter, solved_type_args,
    type_lambda, typed_alone, unify_arguments, walk_unchecked)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import DeclId
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import FuncSig, GenericFuncDef
    from sushi_lang.semantics.passes.types import TypeValidator


#: The faults of an argument that has no type of its own for a reason of its position: an
#: empty `from([])`, a `Maybe.None`. CE2060 stands beside them. Every other fault of an
#: argument is the one fault of the call.
_JOINING_CODES = (er.ERR.CE2111.code, er.ERR.CE2112.code)


class GenericCallee(NamedTuple):
    """What a generic call calls: the signature, the name that the callee row holds, the
    type arguments, and the arguments that the solver checked."""
    sig: FuncSig
    decl: DeclId
    type_args: tuple[Type, ...]
    typed: TypedArguments


def resolve_generic_call(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                         template: GenericFuncDef, written: str) -> Optional[GenericCallee]:
    """The callee of a call of the generic function `template`, or None after its error."""
    if not isinstance(node.target, e.Named):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a generic call has a named target")
    if reject_private_call(validator, "function", template, ref.loc):
        return None
    written_args = node.target.type_args
    if _rejects_a_count(validator, node, ref, template, written, written_args):
        return None
    typed: TypedArguments = NOTHING_TYPED
    if written_args:
        type_args = resolve_explicit_type_args(
            tuple(validator.written_type(arg) for arg in written_args),
            validator.struct_table, validator.enum_table)
    else:
        if any([reject_misplaced_spread(validator, arg) for arg in node.args]):
            return None
        offered = validator.errors_offered()
        joins = _joining_faults(validator)
        solved = _solved(validator, node, template)
        typed = solved.typed
        if solved.type_args is None and any(is_generic_value(validator, arg.value)
                                            for arg in node.args):
            walk_unchecked(validator, node.args, typed)
            return None
        if solved.type_args is None:
            faults = validator.errors_offered() - offered - (_joining_faults(validator) - joins)
            if ((not faults or solved.unreached)
                    and not name_is_contested(validator, "function", template.name)):
                note_opaque(er.emit_with(validator.reporter, er.ERR.CE2060, ref.loc, name=written,
                                         reason=solved.reason), *typed.values()).emit()
            return None
        type_args = solved.type_args
    if validator.in_template_check:
        return _template_callee(validator, node, ref, template, type_args, written, typed)
    return _instance_callee(validator, node, ref, template, type_args, written, typed)


def _rejects_a_count(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                     template: GenericFuncDef, written: str, written_args: tuple) -> bool:
    """CE2062 for a wrong count of written type arguments, else CE2009 for a wrong count of
    arguments, before the solver: a miscount solves nothing. A name that the unit lost has
    no argument count of its own."""
    if written_args:
        expected = check_explicit_type_arg_arity(template, len(written_args))
        if expected is None:
            return False
        loc = node.target.type_args_loc if isinstance(node.target, e.Named) else None
        er.emit(validator.reporter, er.ERR.CE2062, loc or ref.loc, name=written, expected=expected,
                got=len(written_args))
        return True
    if name_is_contested(validator, "function", template.name):
        return False
    count = sum(1 for param in template.params if not param.is_pack)
    has_pack = count != len(template.params)
    fits = len(node.args) >= count if has_pack else len(node.args) == count
    if fits:
        return False
    return not check_arguments(validator, written, [None] * count, node.args, ref.loc,
                               mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                               minimum_arity=has_pack, stop_on_arity=True)


class _Solved(NamedTuple):
    """What the arguments of a call give: the type arguments, or None and the reason. Each
    argument has its answer. `unreached` is True when a type parameter stays open also if
    each argument with an error had solved its parameter: the error is not the one fault."""
    type_args: Optional[tuple[Type, ...]]
    typed: TypedArguments
    reason: str
    unreached: bool


def _solved(validator: TypeValidator, node: e.Call, template: GenericFuncDef) -> _Solved:
    """Each argument has its type with no type from a position, and it unifies with its
    declared parameter: one before the pack parameter through its declared type, and one from
    it on as one element of the pack. An argument with no type, and one that does not unify,
    solves nothing."""
    leading = [param for param in template.params if not param.is_pack]
    names = [tp.name for tp in template.type_params if not tp.is_pack]
    params = [param.ty for param in leading]
    typed = {index: typed_alone(validator, arg) for index, arg in enumerate(node.args)
             if not is_generic_value(validator, arg.value)
             and not (isinstance(arg.value, e.Lambda) and index < len(leading))}
    known = unify_arguments(validator, names, params, range(len(leading)), typed).solved
    for index, arg in enumerate(node.args[:len(leading)]):
        if not isinstance(arg.value, e.Lambda):
            continue
        if is_bare_lambda(validator, arg.value) and names_a_parameter(params[index], names):
            typed[index] = None
        else:
            type_lambda(validator, typed, index, arg,
                        substitute_type_params(params[index], known), names)
    solution = unify_arguments(validator, names, params, range(len(leading)), typed)
    reason = solution.why[0] if solution.why else "could not infer type arguments from call site"
    args = solved_type_args(validator, names, solution.solved)
    if solution.misses or None in typed.values() or None in args:
        faulty = [ty for index, ty in enumerate(params) if typed.get(index) is None]
        unreached = any(arg is None and not any(names_a_parameter(ty, [name]) for ty in faulty)
                        for name, arg in zip(names, args, strict=True))
        return _Solved(None, typed, reason, unreached)
    elements = (as_type_argument(ty) for index, ty in typed.items() if index >= len(leading))
    return _Solved((*args, *elements), typed, reason, False)


def _template_callee(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                     template: GenericFuncDef, type_args: tuple[Type, ...], written: str,
                     typed: TypedArguments) -> Optional[GenericCallee]:
    """A generic call in a check of a template (#1070): the signature at the solved type
    arguments, with no instance. The callee's constraints hold at the type parameters of
    the template only when a constraint of the template promises them."""
    sig = (None if not constraints_hold_in_template(validator, ref.loc, template, type_args)
           else substituted_sig(validator, template, type_args, written))
    if sig is None:
        walk_unchecked(validator, node.args, typed)
        return None
    sig.unit_name = template.unit_name
    return GenericCallee(sig, ref.decl, type_args, typed)


def _instance_callee(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                     template: GenericFuncDef, type_args: tuple[Type, ...], written: str,
                     typed: TypedArguments) -> Optional[GenericCallee]:
    """The signature of the instance in its declaring unit. An instance that the early
    passes did not cut is a late request, and a constraint can refuse it: that refusal is
    the one diagnostic."""
    instance = declared_or_late_instance(validator, ref, template, type_args, written)
    if instance is None:
        walk_unchecked(validator, node.args, typed)
        return None
    return GenericCallee(instance.sig, instance.decl, type_args, typed)


class DeclaredInstance(NamedTuple):
    """The signature of an instance, and the name that a callee row holds for it."""
    sig: FuncSig
    decl: DeclId


def declared_or_late_instance(validator: TypeValidator, ref: e.GlobalRef,
                              template: GenericFuncDef, type_args: tuple[Type, ...],
                              written: str) -> Optional[DeclaredInstance]:
    """The instance that `type_args` name. One that the early passes did not cut is a late
    request. None after the refusal of a constraint, or after CE2061."""
    params = template.type_params
    pack_arity = (len(type_args) - (len(params) - 1) if params[-1].is_pack else None)
    mangled = mangle_function_name(template.name, type_args, pack_arity=pack_arity)
    sig = _declared_instance(validator, template, mangled)
    if sig is None:
        request = validator.tables.request_function_instance
        if request is None:
            er.raise_internal_error("CE0000", span=ref.loc,
                                    detail="the analyzer set no late request of an instance")
        refused = request((template.unit_name, template.name, type_args),
                          (ref.loc, validator.reporter.filename),
                          validator.body_instance_key, validator.reporter)
        sig = _declared_instance(validator, template, mangled)
        if refused or sig is None:
            validator.refused_before = refused or validator.refused_before
            if not refused:
                er.emit(validator.reporter, er.ERR.CE2061, ref.loc, name=written, mangled=mangled,
                        type_args=", ".join(display_type(arg) for arg in type_args))
            return None
    return DeclaredInstance(sig, validator.shir.decls.refer(mangled))


def _declared_instance(validator: TypeValidator, template: GenericFuncDef,
                       mangled: str) -> Optional[FuncSig]:
    """The signature of the instance in the unit of the template, with no fall-back to another
    unit (the identity of an instance holds its unit). The instance of a template of a compiled
    library has no unit in this build."""
    unit = None if template.is_library_template else template.unit_name
    return validator.func_table.declared(mangled, unit)


def _joining_faults(validator: TypeValidator) -> int:
    offered = validator.reporter.errors_offered
    return sum(offered[code] for code in _JOINING_CODES)
