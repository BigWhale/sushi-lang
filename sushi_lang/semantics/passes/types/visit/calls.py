"""The call, text and borrow arms of the typecheck pass over SHIR (docs/design/ir.md 7.3 to 7.6).

A `Call` answers what its callee yields. Its written target decides the callee:

- `Named` of a `GlobalRef` is a call of a free function (`Free`). The function is the
  declaration that the written name of the `GlobalRef` names: behind an alias, what the
  namespace holds (the one resolver of lowering); a bare name, the function of the unit's
  scope. The arguments go through the one argument check (`arguments.check_arguments`),
  which gives each argument its convention. The call yields the `Result` of the channel of
  the function, or its bare return value (`call_yield`). The function decides that type,
  so the call answers it also when an argument does not fit, or when the function is
  private to another unit: a later fault of the value, such as an unhandled `Result`, is a
  fault of its own.
- `BuiltinFn` is `print` or `println` (`Intrinsic`): one argument with a string form, which
  the intrinsic reads (`Guaranteed`). Its display type is an answer.
- `Dotted` is a method call. The receiver is typed first, and the ladder of the call
  (`calls/dotcall.py:resolve_dotted`) finds the callee: a built-in family, a perk
  implementation, an extension method, or nothing (CE2008). A family answer is the callee
  row `Builtin`, and the arm of the family in `FAMILY_ARMS` types the call. A family with
  no arm stops with the label `Call(method <family>)`, for a receiver of any type. A
  method is never variadic, so a bloom argument is CE0120.

A `Static` call on an enum whose dot holds no such member is CE2045
(`visit/results.py:reject_unknown_variant`).

Every other target, and a call that another group types (a generic function, a stdlib
function, a C function, a variadic function, a method of a receiver that is not of a
built-in type and that no family answers, a perk method, an extension method), stops the
walk of its body.

An `Interp` is a `string`, and each hole has a string form; the display type of each hole
is an answer. A `Borrow` (`peek x`, `poke x` outside a call argument) is a value of a
reference type, and the position that holds it decides whether it takes one.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Mapping, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ffi_boundary import ERRNO_FUNCTION
from sushi_lang.semantics.generics.contracts import DISPLAY
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.opaque import explain_unpromised
from sushi_lang.semantics.generics.primitives import primitive_method_return_type
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.maybe import (
    MAYBE_METHOD_SIGNATURES, WrapperMethodRow, maybe_method_row, maybe_payload)
from sushi_lang.semantics.generics.results import RESULT_METHOD_SIGNATURES, result_method_row
from sushi_lang.semantics.method_effects import (
    MAYBE_FAMILY, PRIMITIVE_FAMILY, RESULT_FAMILY, STRING_FAMILY)
from sushi_lang.semantics.param_modes import Convention, declared_modes, param_mode
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    Parameters, check_arguments, reject_misplaced_spread)
from sushi_lang.semantics.passes.types.calls.dotcall import (
    MethodRung, reject_unknown_method, resolve_dotted)
from sushi_lang.semantics.passes.types.calls.user_defined import own_concrete_function
from sushi_lang.semantics.passes.types.method_registry import (
    MethodFamily, string_method_return_type)
from sushi_lang.semantics.passes.types.expressions import top_level_contract
from sushi_lang.semantics.passes.types.resolution import call_yield, resolve_variable_type
from sushi_lang.semantics.passes.types.utils import names_no_type
from sushi_lang.semantics.passes.types.visibility import (
    name_is_contested, name_was_refused, out_of_scope_help, reject_ambiguous_name,
    reject_private_call, reject_private_kept_call, type_name_is_contested)
from sushi_lang.semantics.passes.types.visit.helpers import function_type_of_sig
from sushi_lang.semantics.passes.types.visit.results import reject_unknown_variant
from sushi_lang.semantics.shir import dispatch, exprs as e
from sushi_lang.semantics.shir.callee import Builtin, Free, Intrinsic
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.type_predicates import is_instance_of, is_string_convertible
from sushi_lang.semantics.typesys import (
    BuiltinType, DynamicArrayType, ReferenceType, Type, deref_type)

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import FuncSig, Param
    from sushi_lang.semantics.passes.types import TypeValidator


STRING_ARRAY_STOP = "string array"


def check_call(validator: TypeValidator, node: e.Call,
               expected: Optional[Type]) -> Optional[Type]:
    target = node.target
    if isinstance(target, e.BuiltinFn):
        return _check_print(validator, node, target.name)
    if isinstance(target, e.Dotted):
        return _check_method_call(validator, node, target)
    if (isinstance(target, e.Named) and isinstance(target.ref, e.GlobalRef)
            and not target.type_args):
        return _check_free_call(validator, node, target.ref)
    if isinstance(target, e.Static) and reject_unknown_variant(validator, node, target,
                                                               expected):
        return None
    visit.stop_at(validator, node)


def _check_free_call(validator: TypeValidator, node: e.Call,
                     ref: e.GlobalRef) -> Optional[Type]:
    written = validator.decl_name(ref.decl)
    alias, dot, name = written.rpartition(".")
    sig = (_function_behind_alias(validator, node, alias, name) if dot
           else _function_of_scope(validator, node, ref, written))
    if sig is None:
        return None
    if reject_private_call(validator, "function", sig, ref.loc):
        return call_yield(validator, sig.ret_type, sig.err_type)
    if (sig.borrowed_return is not None
            or any(p.is_variadic or p.is_pack or p.ty is None for p in sig.params)):
        visit.stop_at(validator, node)
    answers = validator.body_answers()
    answers.record_callee(node.shir_id, ResolvedCall(Free(ref.decl), unit=sig.unit_name))
    function_type = function_type_of_sig(sig)
    if function_type is not None:
        visit.answer(validator, ref, function_type)
    params = [_parameter_type(validator, param) for param in sig.params]
    declared = Parameters(node.shir_id, declared_modes(sig.params),
                          [param.name for param in sig.params])
    check_arguments(validator, written, params, node.args, ref.loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009, declared=declared)
    return call_yield(validator, sig.ret_type, sig.err_type)


def _check_method_call(validator: TypeValidator, node: e.Call,
                       target: e.Dotted) -> Optional[Type]:
    """`x.f(args)`: the receiver, then the callee that the ladder finds (ir.md 7.3).

    A family answer goes to the arm of the family in `FAMILY_ARMS`, and a family with no
    arm stops the walk with the label of the family, on a receiver of any type. A perk
    implementation and an extension method stop the walk (C6, A10), and so does a method
    of a receiver that is not of a built-in type. A method that nothing answers on a local
    whose `let` the pass refused reports nothing more.
    """
    refused = [reject_misplaced_spread(validator, arg) for arg in node.args]
    if any(refused):
        return None
    received = visit.check_expr(validator, target.recv)
    if received is None:
        return None
    receiver = deref_type(received)
    answer = resolve_dotted(validator, receiver, target.name)
    if answer.family is not None:
        arm = FAMILY_ARMS.get(answer.family.name)
        if arm is None:
            dispatch.stop_at_family(visit.STAGE, node, answer.family.name)
        callee = Builtin(target.recv, answer.family.callee(target.name))
        validator.body_answers().record_callee(node.shir_id, ResolvedCall(callee))
        return arm(validator, node, receiver, target.name, answer.family)
    if not isinstance(receiver, BuiltinType):
        visit.stop_at(validator, node)
    if answer.rung is MethodRung.UNANSWERED:
        if not (isinstance(target.recv, e.LocalRef)
                and target.recv.local in validator.refused_locals):
            reject_unknown_method(validator, receiver, target.name, node.loc)
        return None
    visit.stop_at(validator, node)


def _check_primitive_method(validator: TypeValidator, node: e.Call, receiver: Type,
                            name: str, family: MethodFamily) -> Optional[Type]:
    """A primitive method takes no argument and yields the type of its row. A wrong count
    is CE2009, and the call still yields that type."""
    reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                            f"{display_type(receiver)}.{name}", family.arity)
    return primitive_method_return_type(receiver, name)


def _check_string_method(validator: 'TypeValidator', node: e.Call, receiver: Type,
                         name: str, family: MethodFamily) -> Optional[Type]:
    """A string method takes the arguments of its row and yields the type of its row.

    A wrong count is CE2009 and skips the argument types; a wrong argument type is
    CE2006 at the argument. The call yields its type after either fault. A method that
    answers a dynamic array stops the walk, as the array values are not typed yet.
    """
    from sushi_lang.sushi_stdlib.src.collections.strings import METHOD_SPECS
    spec = METHOD_SPECS.get(name)
    miscount = reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                                       f"{display_type(receiver)}.{name}", family.arity)
    if not miscount and spec is not None:
        _check_builtin_arguments(validator, node, name, family, spec.arg_types,
                                 er.ERR.CE2006, callee_name=spec.name,
                                 also_accepts=spec.also_accepts)
    yielded = string_method_return_type(validator, name)
    if isinstance(yielded, DynamicArrayType):
        dispatch.stop_at_family(visit.STAGE, node, STRING_ARRAY_STOP)
    return yielded


def _check_builtin_arguments(validator: TypeValidator, node: e.Call, name: str,
                             family: MethodFamily, params: Sequence[Type],
                             mismatch_code: er.ErrorMessage, *,
                             callee_name: Optional[str] = None,
                             also_accepts: Sequence[Sequence[Type]] = ()) -> None:
    """The arguments of the built-in method `name` whose count fits, through the one
    argument check: each argument at the type of its row, with the mode of its row."""
    modes = family.modes.of(name, len(node.args)) or ()
    declared = Parameters(node.shir_id, modes, [f"#{i}" for i in range(1, len(modes) + 1)])
    check_arguments(validator, callee_name or name, params, node.args, node.loc,
                    mismatch_code=mismatch_code, arity_code=er.ERR.CE2009,
                    declared=declared, also_accepts=also_accepts)


def _check_result_method(validator: TypeValidator, node: e.Call, receiver: Type,
                         name: str, family: MethodFamily) -> Optional[Type]:
    """A method of `Result@(T, E)`. A method with a signature row (`map_err`) stops the
    walk with the label of the family, as it is not typed yet."""
    if name in RESULT_METHOD_SIGNATURES:
        dispatch.stop_at_family(visit.STAGE, node, family.name)
    row = result_method_row(receiver, name, validator.enum_table,
                            validator.struct_table.by_name)
    return _check_wrapper_method(validator, node, receiver, name, family, row)


def _check_maybe_method(validator: TypeValidator, node: e.Call, receiver: Type,
                        name: str, family: MethodFamily) -> Optional[Type]:
    """A method of `Maybe@(T)`. A method with a signature row (`or_err`) stops the walk
    with the label of the family, as it is not typed yet."""
    if name in MAYBE_METHOD_SIGNATURES:
        dispatch.stop_at_family(visit.STAGE, node, family.name)
    row = maybe_method_row(maybe_payload(receiver), name)
    return _check_wrapper_method(validator, node, receiver, name, family, row)


def _check_wrapper_method(validator: TypeValidator, node: e.Call, receiver: Type,
                          name: str, family: MethodFamily,
                          row: Optional[WrapperMethodRow]) -> Optional[Type]:
    """A method of a `Result` or a `Maybe` takes the arguments of its row and yields the
    type of its row.

    A wrong count is CE2009 and skips the argument types. `realise` on a `~` payload is
    CE2506, and its default is not checked. An argument of another type than its row is
    CE2503 at the argument. The call yields its type after each fault.
    """
    if row is None:
        raise_internal_error("CE0094", method=name)
    if reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                               f"{display_type(receiver)}.{name}", family.arity):
        return row.returns
    if name == "realise" and row.returns == BuiltinType.BLANK:
        er.emit(validator.reporter, er.ERR.CE2506, node.loc, ty=display_type(receiver))
        return row.returns
    _check_builtin_arguments(validator, node, name, family, row.params, er.ERR.CE2503)
    return row.returns


FamilyArm = Callable[['TypeValidator', e.Call, Type, str, MethodFamily], Optional[Type]]

#: The arm of each built-in family that the pass types, keyed by the registry name of the
#: family. A family that is not here stops the walk (`Call(method <family>)`).
FAMILY_ARMS: Mapping[str, FamilyArm] = MappingProxyType({
    PRIMITIVE_FAMILY: _check_primitive_method,
    STRING_FAMILY: _check_string_method,
    RESULT_FAMILY: _check_result_method,
    MAYBE_FAMILY: _check_maybe_method,
})


def _function_of_scope(validator: TypeValidator, node: e.Call, ref: e.GlobalRef,
                       name: str) -> Optional[FuncSig]:
    """The concrete function that a bare name calls in this unit, or None after its error.

    A generic function, a stdlib function and `errno()` are not typed here, and their call
    stops the walk of its body. A bare call that constructs a struct is a `StructInit`
    (lowering, #1076), never a call.
    """
    own = own_concrete_function(validator.func_table, name, validator.current_unit_name)
    if ((own is None and validator.generic_sig(name) is not None)
            or type_name_is_contested(validator, name)):
        visit.stop_at(validator, node)
    sig = validator.func_sig(name)
    if sig is None:
        if (validator.func_table.lookup_stdlib_by_name(name, validator.scope) is not None
                or name == ERRNO_FUNCTION):
            visit.stop_at(validator, node)
        reject_undefined_function(validator, name, ref.loc)
        return None
    if reject_ambiguous_name(validator, "function", name, ref.loc):
        return None
    if (name_is_contested(validator, "function", name)
            and sig.unit_name != validator.current_unit_name):
        visit.stop_at(validator, node)
    return sig


def _function_behind_alias(validator: TypeValidator, node: e.Call, alias: str,
                           name: str) -> FuncSig:
    """The function that `alias.name` names: what the namespace holds under the name."""
    binding = validator.namespaces.lookup(alias, name)
    if (binding is None or binding.kind != "function"
            or binding.provider.namespace_kind in ("extern", "stdlib")):
        visit.stop_at(validator, node)
    return binding.record


def reject_undefined_function(validator: TypeValidator, name: str, loc: Span) -> None:
    """CE2008 for a name that no function of the unit's scope takes.

    A name that a library keeps is refused as kept (#469), and a name that the unit
    declared under another kind was refused at its declaration (#1102).
    """
    kept = validator.library_not_exported.get(name)
    if kept is not None and reject_private_kept_call(validator, name, loc, library=kept[0],
                                                     kind=kept[1]):
        return
    if name_was_refused(validator, name):
        return
    diag = er.emit_with(validator.reporter, er.ERR.CE2008, loc, name=name)
    missing = out_of_scope_help(validator, "function", name)
    if missing is not None:
        diag.help(missing)
    diag.emit()


def _parameter_type(validator: TypeValidator, param: Param) -> Type:
    """The declared type of a parameter, resolved; a `peek` or a `poke` parameter is a
    borrow of the resolved type."""
    mode = param_mode(param)
    span = param.type_span or param.name_span
    resolved = resolve_variable_type(validator, deref_type(param.ty), span)
    return ReferenceType(resolved, mode.borrow_mode) if mode.by_pointer else resolved


def _check_print(validator: TypeValidator, node: e.Call, name: str) -> Optional[Type]:
    """`print(v)` and `println(v)`: the intrinsic reads one value with a string form.

    An unhandled `Result` is CE2037, and any other value with no string form is CE2115.
    A `peek` or a `poke` marker on the argument reads the value too.
    """
    answers = validator.body_answers()
    answers.record_callee(node.shir_id, ResolvedCall(Intrinsic(name)))
    (arg,) = node.args
    answers.record_convention(node.shir_id, 0, Convention.GUARANTEED)
    ty = visit.check_expr(validator, arg.value)
    if ty is None:
        return None
    if is_instance_of(ty, "Result"):
        er.emit(validator.reporter, er.ERR.CE2037, arg.value.loc)
        return None
    if reject_no_string_form(validator, ty, arg.value.loc, printed=True):
        return None
    answers.record_display(node.shir_id, 0, deref_type(ty))
    return BuiltinType.BLANK


def check_interp(validator: TypeValidator, node: e.Interp,
                 _expected: Optional[Type]) -> Optional[Type]:
    """An interpolated string: each hole has a string form (CE2035)."""
    answers = validator.body_answers()
    refused = False
    for index, part in enumerate(node.parts):
        if isinstance(part, str):
            continue
        ty = visit.check_expr(validator, part)
        if ty is None or reject_no_string_form(validator, ty, part.loc):
            refused = True
            continue
        answers.record_display(node.shir_id, index, deref_type(ty))
    return None if refused else BuiltinType.STRING


def reject_no_string_form(validator: TypeValidator, ty: Type, loc: Span, *,
                          printed: bool = False) -> bool:
    """A value with no string form: CE2115 for `print`, CE2035 for a hole. True when it
    was refused.

    A primitive has a string form, and so does a struct, an enum, an array, a `List@(T)`
    and an `Own@(T)` through `Display`; `print` takes `~` too. A type that a written-type
    check refused was reported there. A type that holds something with no string form
    says which field or element it is.
    """
    shown = deref_type(ty)
    if (is_string_convertible(shown) or (printed and shown == BuiltinType.BLANK)
            or names_no_type(validator, shown)):
        return False
    refused: list = []
    printable, reason = top_level_contract(validator, ty, DISPLAY, refused)
    if printable:
        return False
    spelled = display_type(ty)
    report = (er.emit_with(validator.reporter, er.ERR.CE2115, loc, type=spelled) if printed
              else er.emit_with(validator.reporter, er.ERR.CE2035, loc, type=spelled))
    if reason is not None:
        report = report.note(f"no derived Display: {reason}")
    explain_unpromised(report, DISPLAY, refused).emit()
    return True


def check_borrow(validator: TypeValidator, node: e.Borrow,
                 expected: Optional[Type]) -> Optional[Type]:
    """`peek x` or `poke x` outside a call argument: a borrow of the type of `x`."""
    ty = visit.check_expr(validator, node.place, deref_type(expected))
    return None if ty is None else ReferenceType(ty, node.mode)
