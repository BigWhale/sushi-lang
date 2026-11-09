"""The arms of a function used as a value and of a call of a function value (docs/design/ir.md
7.3, 7.6).

A `GlobalRef` that no call target holds is a named function used as a value. It answers the
function type of the function, with no capture, and the callee row `Free` keyed by the
`GlobalRef`. A generic function names the instance that the function type of its position
solves; an instance that the early passes did not cut is a late request. A C global behind an
alias stops the walk. A call whose target is any other value is an `Indirect` callee: the
function type of the value gives each argument its type and its convention. A field of a
function type that no method of the struct names is an `Indirect` callee too. A name of a
constant or of a `var` is no callee (CE2008), and any other value that is no function is
CE2092.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import CalleeKind, effective_modes
from sushi_lang.semantics.passes.collect.functions import FuncSig, GenericFuncDef
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import Parameters, check_arguments
from sushi_lang.semantics.passes.types.borrowed_returns import reject_borrowed_function_value
from sushi_lang.semantics.passes.types.calls.generics import (
    constraints_hold_in_template, reject_unsolved_generic_value, solved_value_type_args,
    substituted_sig)
from sushi_lang.semantics.passes.types.calls.namespaced import reject_unknown_namespace_call
from sushi_lang.semantics.passes.types.calls.user_defined import own_concrete_function
from sushi_lang.semantics.passes.types.method_registry import MethodFamily
from sushi_lang.semantics.passes.types.resolution import call_yield, resolve_variable_type
from sushi_lang.semantics.passes.types.visibility import (
    name_was_refused, reject_ambiguous_name, reject_private_name, reject_undefined_function)
from sushi_lang.semantics.passes.types.visit.generic_calls import declared_or_late_instance
from sushi_lang.semantics.passes.types.visit.helpers import function_type_of_sig
from sushi_lang.semantics.shir import dispatch, exprs as e
from sushi_lang.semantics.shir.callee import Free, Indirect
from sushi_lang.semantics.shir.ids import DeclId
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.passes.types.visit.user_callees import user_callee
from sushi_lang.semantics.typesys import FunctionType, Type, deref_type
from sushi_lang.semantics.visibility import VALUE_VERB

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_global_ref(validator: TypeValidator, node: e.GlobalRef,
                     expected: Optional[Type]) -> Optional[Type]:
    written = validator.decl_name(node.decl)
    alias, dot, name = written.rpartition(".")
    return (_behind_alias(validator, node, alias, name, expected) if dot
            else _bare(validator, node, written, expected))


def _bare(validator: TypeValidator, node: e.GlobalRef, name: str,
          expected: Optional[Type]) -> Optional[Type]:
    """The own concrete function of the unit wins, then a generic function of the scope, then
    a function that an import brings, with the fences of its call (CE3005, CE3012)."""
    own = own_concrete_function(validator.func_table, name, validator.current_unit_name)
    if own is not None:
        return _concrete(validator, node, own, name)
    template = validator.generic_sig(name)
    if template is not None:
        return _generic(validator, node, template, name, expected)
    sig = validator.func_sig(name)
    if sig is None:
        visit.stop_at(validator, node)
    if (reject_private_name(validator, "function", sig, node.loc, verb=VALUE_VERB)
            or reject_ambiguous_name(validator, "function", name, node.loc)):
        return None
    return _concrete(validator, node, sig, name)


def _behind_alias(validator: TypeValidator, node: e.GlobalRef, alias: str, name: str,
                  expected: Optional[Type]) -> Optional[Type]:
    """What the namespace holds under the name. A function of a stdlib module and a C function
    are no value (CE2008)."""
    binding = validator.namespaces.lookup(alias, name)
    if binding is not None and binding.kind == "extern variable":
        visit.stop_at(validator, node)
    if (binding is None or binding.provider.namespace_kind != "unit"
            or binding.kind not in ("function", "generic function")):
        reject_unknown_namespace_call(validator, alias, alias, name, node.loc)
        return None
    written = f"{alias}.{name}"
    if binding.kind == "generic function":
        return _generic(validator, node, binding.record, written, expected)
    if reject_private_name(validator, "function", binding.record, node.loc, verb=VALUE_VERB):
        return None
    return _concrete(validator, node, binding.record, written)


def _concrete(validator: TypeValidator, node: e.GlobalRef, sig: FuncSig,
              written: str) -> Optional[Type]:
    if reject_borrowed_function_value(validator, sig, written, node.loc):
        return None
    return _value(validator, node, sig, node.decl)


def _generic(validator: TypeValidator, node: e.GlobalRef, template: GenericFuncDef, written: str,
             expected: Optional[Type]) -> Optional[Type]:
    """The instance that the function type `expected` solves (CE2093 when it solves nothing).
    A constraint that refuses the solved type arguments is the one diagnostic."""
    if reject_borrowed_function_value(validator, template, written, node.loc):
        return None
    type_args = solved_value_type_args(validator, template, expected)
    if type_args is None:
        reject_unsolved_generic_value(validator, node.loc, written, expected)
        return None
    if validator.in_template_check:
        if not constraints_hold_in_template(validator, node.loc, template, type_args):
            return None
        sig = substituted_sig(validator, template, type_args, written)
        if sig is None:
            return None
        sig.unit_name = template.unit_name
        return _value(validator, node, sig, node.decl, type_args)
    instance = declared_or_late_instance(validator, node, template, type_args, written)
    if instance is None:
        return None
    return _value(validator, node, instance.sig, instance.decl, type_args)


def _value(validator: TypeValidator, node: e.GlobalRef, sig: FuncSig, decl: DeclId,
           type_args: tuple[Type, ...] = ()) -> Optional[Type]:
    """The callee row and the type of the value. A function value of a named function captures
    nothing, so the value owns nothing. A private function of a binary library has its own
    link symbol. A variadic function has no function type yet, so it stops."""
    function_type = function_type_of_sig(sig)
    if function_type is None:
        dispatch.stop(visit.STAGE, node, state="of a variadic function")
    validator.body_answers().record_callee(node.shir_id, user_callee(Free(decl), sig, type_args))
    return replace(resolve_variable_type(validator, function_type, node.loc), captures=())


def function_behind_local(validator: TypeValidator, node: e.Call) -> Optional[e.GlobalRef]:
    """The function that a call of a local names when the local holds no function: the name is
    the function of the scope, as a local does not shadow it for a call."""
    target = node.target
    if not (isinstance(target, e.Named) and isinstance(target.ref, e.LocalRef)):
        return None
    local = validator.body_locals[target.ref.local]
    if isinstance(validator.body_answers().local_type(target.ref.local), FunctionType):
        return None
    if validator.func_sig(local.name) is None and validator.generic_sig(local.name) is None:
        return None
    return e.GlobalRef(target.ref.shir_id, target.ref.loc, validator.shir.decls.refer(local.name))


def check_value_call(validator: TypeValidator, node: e.Call, value: e.ShirExpr) -> Optional[Type]:
    """`f(args)` for a target that is no named function. A name of a constant or of a `var`
    and a local that holds no function are no callee (CE2008), as a name with no function."""
    if isinstance(value, (e.ConstRef, e.StorageRef)):
        name = validator.decl_name(value.const_id if isinstance(value, e.ConstRef) else value.decl)
        reject_undefined_function(validator, name, value.loc)
        validator.refused_before = validator.refused_before or name_was_refused(validator, name)
        return None
    called = visit.check_expr(validator, value)
    if isinstance(called, FunctionType):
        validator.body_answers().record_callee(node.shir_id, ResolvedCall(Indirect(value)))
        return _value_arguments(validator, node, called, value.loc)
    if isinstance(value, e.LocalRef):
        if called is not None:
            reject_undefined_function(validator, validator.body_locals[value.local].name,
                                      value.loc)
        return None
    er.emit(validator.reporter, er.ERR.CE2092, value.loc, expected="a function value",
            actual=display_type(called) if called is not None else "a non-function expression")
    return None


def check_field_call(validator: TypeValidator, node: e.Call, target: e.Dotted, receiver: Type,
                     called: FunctionType) -> Optional[Type]:
    """`x.f(args)` through the field `f` of the function type `called`."""
    index = [name for name, _ in receiver.fields].index(target.name)
    answers = validator.body_answers()
    answers.record_callee(node.shir_id, ResolvedCall(Indirect(target.recv, target.name)))
    answers.record_field_index(node.shir_id, index)
    return _value_arguments(validator, node, resolve_variable_type(validator, called, node.loc),
                            node.loc)


def _value_arguments(validator: TypeValidator, node: e.Call, called: FunctionType,
                     loc: Span) -> Optional[Type]:
    """The arguments of a call of a function value. A wrong count says no more about the
    arguments. The function type gives each parameter its type and its mode. The call yields
    what a direct call yields."""
    yields = call_yield(validator, called.ok_type, called.err_type)
    if len(node.args) != len(called.param_types):
        er.emit(validator.reporter, er.ERR.CE2092, loc, expected=display_type(called),
                actual=f"a call with {len(node.args)} argument(s)")
        return yields
    modes = effective_modes(called.modes, CalleeKind.INDIRECT)
    declared = Parameters(node.shir_id, modes,
                          [f"#{index + 1}" for index in range(len(node.args))])
    check_arguments(validator, display_type(called), called.param_types, node.args, loc,
                    mismatch_code=er.ERR.CE2092, arity_code=er.ERR.CE2092, declared=declared)
    return yields


def check_function_clone(validator: TypeValidator, node: e.Call, receiver: Type, name: str,
                         family: MethodFamily) -> Type:
    """`f.clone()` yields the type of `f` and takes no argument (CE2009)."""
    reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                            f"{display_type(deref_type(receiver))}.{name}", family.arity)
    return receiver
