"""Generic function call validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import declared_modes
from sushi_lang.semantics.typesys import UnknownType
from sushi_lang.semantics.ast import Call
from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.explicit_type_args import (
    resolve_explicit_type_args,
    check_explicit_type_arg_arity,
)
from ..visibility import name_is_contested, reject_private_call
from ..arguments import check_arguments, reject_misplaced_spread
from .user_defined import validate_call_arguments

if TYPE_CHECKING:
    from .. import TypeValidator


def validate_generic_function_call(
    validator: 'TypeValidator',
    call: Call,
    function_name: str,
    generic_func=None,
    written_name: str | None = None,
) -> None:
    """Validate generic function call and rewrite to use mangled name.

    A qualified call (`geo.twin(...)`) resolved its declaration through the alias's
    provider already and hands it in; a bare call resolves through the unit ladder
    here (#495). `written_name` is the callee as the user wrote it (`geo.twin`), for
    every diagnostic to quote; `function_name` is the declaration's own name.
    """
    written = written_name or function_name

    if generic_func is None:
        generic_func = validator.generic_sig(function_name)
    if generic_func is None:
        generic_func = validator.generic_func_table.by_name[function_name]

    # Visibility first: nothing below it is worth saying. A source library's units are
    # ordinary units here, so the call resolves and only the backend used to notice --
    # and it noticed as a KeyError (#467).
    if reject_private_call(validator, "function", generic_func, call.callee.loc):
        return

    explicit = call.type_args
    if explicit:
        # Explicit `@(...)` type args override inference (issue #137).
        expected = check_explicit_type_arg_arity(generic_func, len(explicit))
        if expected is not None:
            er.emit(
                validator.reporter,
                er.ERR.CE2062,
                call.type_args_loc or call.callee.loc,
                name=written,
                expected=expected,
                got=len(explicit),
            )
            return

    contested = name_is_contested(validator, "function", function_name)
    if not explicit and not contested and _reject_argument_count(
            validator, call, generic_func, written):
        return

    why: list[str] = []
    type_args = _named_or_inferred_type_args(validator, call, generic_func, why)
    if type_args is None:
        said = _untyped_argument_faults(validator)
        _walk_unchecked_arguments(validator, call)
        # An argument that has no type of its own (a generic function value, CE2093; a
        # type-pack name, CE0144) is why nothing was inferred: its diagnostic is the one
        # fault (#1105, #1109). Any other argument fault stands beside CE2060.
        if not contested and _untyped_argument_faults(validator) == said:
            er.emit(
                validator.reporter,
                er.ERR.CE2060,
                call.callee.loc,
                name=written,
                reason=why[0] if why else "could not infer type arguments from call site"
            )
        return

    # Generate mangled name. When the function's LAST type-param is a pack, the
    # symbol carries the pack arity so it matches the monomorphizer's ".pack{N}"
    # name (mirrors monomorphize/functions.py).
    type_params = generic_func.type_params or []
    has_pack = bool(type_params) and type_params[-1].is_pack
    if has_pack:
        pack_arity = len(type_args) - (len(type_params) - 1)
        mangled_name = mangle_function_name(
            function_name, type_args, pack_arity=pack_arity
        )
    else:
        mangled_name = mangle_function_name(function_name, type_args)

    # The instance is parked in the DECLARING unit (D3), which an aliased import
    # keeps out of the caller's flat scope -- so the lookup asks that unit directly.
    home_unit = getattr(generic_func, "unit_name", None)
    func_sig = validator.func_table.lookup(mangled_name, home_unit)
    if func_sig is None:
        _walk_unchecked_arguments(validator, call)
        er.emit(
            validator.reporter,
            er.ERR.CE2061,
            call.callee.loc,
            name=written,
            mangled=mangled_name,
            type_args=", ".join(display_type(t) for t in type_args)
        )
        return

    call.callee.id = mangled_name

    # The WRITTEN name, not the instance's symbol: the user never wrote `pair__i32` (#766).
    validate_call_arguments(validator, written, func_sig,
                            call.args, call.callee.loc)


def _reject_argument_count(validator: 'TypeValidator', call: Call, generic_func,
                           written: str) -> bool:
    """CE2009 for a wrong argument count, asked BEFORE inference (#790).

    A miscount cannot be solved, and CE2060 then spoke about inference where the user
    miscounted. The count is the template's: its fixed parameters, and at least that
    many when a pack parameter takes the rest.

    The check measures the COUNT and walks no argument. Each argument is walked one time
    per call: by the check of the solved instance, or by `_walk_unchecked_arguments` when
    no instance is solved. A walk here too reported a fault in an argument two times
    (#1037).
    """
    count = sum(1 for p in generic_func.params if not p.is_pack)
    has_pack = count != len(generic_func.params)
    args = call.args
    if (len(args) >= count) if has_pack else (len(args) == count):
        return False
    return not check_arguments(
        validator, written, [None] * count, args, call.callee.loc,
        mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
        minimum_arity=has_pack, stop_on_arity=True)


_UNTYPED_ARGUMENT_CODES = (er.ERR.CE2093.code, er.ERR.CE0144.code)


def _untyped_argument_faults(validator: 'TypeValidator') -> int:
    offered = validator.reporter.errors_offered
    return sum(offered[code] for code in _UNTYPED_ARGUMENT_CODES)


def _walk_unchecked_arguments(validator: 'TypeValidator', call: Call) -> None:
    """Walk each argument when no instance is solved, so a fault in one is still said.

    The check of the instance is the walk on the solved path; this is the walk on the
    other path, and the two never run for one call.
    """
    for arg in call.args:
        if not reject_misplaced_spread(validator, arg):
            validator.validate_expression(arg)


def call_type_args(validator: 'TypeValidator', call: Call, generic_func) -> Optional[tuple]:
    """The type arguments a generic call site names -- explicit, or inferred. Silent.

    ONE reader, because the validating half and the inferring half must agree: the symbol
    the call is rewritten to and the type its result is stamped with are both read off
    these arguments. A wrong explicit arity answers None here and CE2062 there.
    """
    if call.type_args and check_explicit_type_arg_arity(
            generic_func, len(call.type_args)) is not None:
        return None
    return _named_or_inferred_type_args(validator, call, generic_func)


def _named_or_inferred_type_args(validator: 'TypeValidator', call: Call, generic_func,
                                 why: Optional[list[str]] = None) -> Optional[tuple]:
    """The explicit type arguments resolved, or the inferred ones. The arity is checked.

    `why` collects the reason an inference failed, for CE2060.
    """
    if call.type_args:
        return resolve_explicit_type_args(
            call.type_args, validator.struct_table.by_name, validator.enum_table.by_name)
    return _infer_type_args_from_call_site(validator, call, generic_func, why)


def generic_call_result_type(validator: 'TypeValidator', call: Call, generic_func):
    """What a call to a generic function yields, before its instance exists (#556).

    A call is normally typed through the callee's concrete signature, and a generic
    callee has none until the monomorphize pass builds one under a mangled symbol. The
    inferrer runs before that symbol is chosen -- a generic call sitting in another
    generic call's ARGUMENT is typed while the outer call solves its own parameters --
    and answering nothing there left the argument untyped, so the outer call was CE2060
    however the value was spelled. The substituted signature answers instead, the same
    derivation the instantiate pass reads for a `match` scrutinee (#549).
    """
    from sushi_lang.semantics.generics.types import substituted_call_result

    type_args = call_type_args(validator, call, generic_func)
    if type_args is None:
        return None
    return substituted_call_result(generic_func, type_args)


def reject_unsolved_generic_value(validator: 'TypeValidator', loc, name: str,
                                  expected_ty) -> None:
    """CE2093 for a generic function value that no stated function type solves.

    The one text for the bare name and the name behind an alias (#1105).
    """
    from sushi_lang.semantics.typesys import FunctionType
    if isinstance(expected_ty, FunctionType):
        reason = (f"the function type '{display_type(expected_ty)}' of this position "
                  "does not solve its type arguments")
        help_text = (f"state a function type whose parameter types solve each type "
                     f"parameter of '{name}'")
    else:
        reason = "this position states no function type, so its type arguments are unknown"
        help_text = (f"state the function type at the position, for example "
                     f"'let fn(i32) -> i32 g = {name}'")
    er.emit_with(validator.reporter, er.ERR.CE2093, loc, name=name,
                 reason=reason).help(help_text).emit()


def resolve_generic_fn_reference(validator: 'TypeValidator', name: str, expected_ty,
                                 generic_func=None):
    """Resolve a generic-fn reference against an expected FunctionType (T2.3).

    A bare name reads the unit's own view; a name behind an alias hands in the
    declaration its provider resolved (#1017).
    """
    from sushi_lang.semantics.typesys import FunctionType
    from sushi_lang.semantics.generics.pack_inference import solve_leading_type_args
    if not isinstance(expected_ty, FunctionType):
        return None
    if generic_func is None:
        generic_func = validator.generic_sig(name)
    if generic_func is None:
        return None
    type_params = generic_func.type_params or []
    if type_params and type_params[-1].is_pack:
        return None
    type_args = solve_leading_type_args(
        generic_func, list(expected_ty.param_types),
        validator.struct_table.by_name, validator.enum_table.by_name,
        ret_type=expected_ty.ok_type)
    if type_args is None:
        return None

    mangled_name = mangle_function_name(name, type_args)
    func_sig = validator.func_table.lookup(
        mangled_name, getattr(generic_func, "unit_name", None))
    if func_sig is None:
        return None
    param_types = tuple(p.ty for p in func_sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = func_sig.ret_type
    concrete_ft = FunctionType(param_types=param_types, ok_type=ok_type,
                               err_type=func_sig.err_type,
                               param_modes=declared_modes(func_sig.params))
    return mangled_name, concrete_ft


def _infer_type_args_from_call_site(
    validator: 'TypeValidator',
    call: Call,
    generic_func,
    why: Optional[list[str]] = None,
) -> Optional[tuple]:
    """Infer type arguments from call site arguments.

    A generic function value is not typed here: the callee types it, once the other
    arguments solve the callee (#1029).
    """
    from sushi_lang.semantics.generics.pack_inference import infer_flat_type_args
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    call_args = getattr(call, "args", []) or []
    arg_types: list = []
    for arg_expr in call_args:
        if names_generic_fn_value(validator, arg_expr):
            arg_types.append(None)
            continue
        arg_type = validator.infer_expression_type(arg_expr)
        if arg_type is None or isinstance(arg_type, UnknownType):
            return None
        arg_types.append(resolve_unknown_type(arg_type, structs, enums))

    return infer_flat_type_args(generic_func, arg_types, structs, enums, why)


def names_generic_fn_value(validator: 'TypeValidator', expr) -> bool:
    """The expression is a generic function written as a value, bare or behind an alias.

    A local and the unit's own concrete function win the bare name, as they do in
    `visit_name`.
    """
    from sushi_lang.semantics.ast import MemberAccess, Name
    from .user_defined import own_concrete_function
    if isinstance(expr, Name):
        return (expr.id not in validator.variable_types
                and own_concrete_function(validator.func_table, expr.id,
                                          validator.current_unit_name) is None
                and validator.generic_sig(expr.id) is not None)
    if isinstance(expr, MemberAccess) and isinstance(expr.receiver, Name):
        if expr.receiver.id in validator.variable_types:
            return False
        binding = validator.resolve_namespaced(expr.receiver, expr.member)
        return binding is not None and binding.kind == "generic function"
    return False
