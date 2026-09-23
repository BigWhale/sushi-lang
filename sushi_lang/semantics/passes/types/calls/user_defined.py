"""User-defined and stdlib function call validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.ast import Call, Name, Spread
from ..visibility import (name_is_contested, out_of_scope_help,
                          reject_ambiguous_name, reject_private_call,
                          reject_private_kept_call)
from ..arguments import check_arguments
from ..compatibility import types_compatible
from ..propagation import propagate_types_to_value
from sushi_lang.semantics.type_predicates import (
    BUILTIN_FLOAT_TYPES, BUILTIN_INTEGER_TYPES, BUILTIN_NUMERIC_TYPES,
    BUILTIN_UNSIGNED_INTEGER_TYPES)

if TYPE_CHECKING:
    from .. import TypeValidator


def validate_variadic_trailing_args(validator: 'TypeValidator', trailing: list,
                                    fixed_count: int, array_ty, element_ty) -> None:
    """Validate the trailing arguments of a variadic call (native '...T' or stdlib)."""
    for offset, arg in enumerate(trailing):
        index = fixed_count + offset + 1
        if isinstance(arg, Spread):
            if offset != 0 or len(trailing) != 1:
                er.emit(validator.reporter, er.ERR.CE0120, arg.loc,
                        message="bloom argument 'arr...' must be the sole, last trailing argument")
            elif not isinstance(arg.value, Name):
                # The backend only marks a bloomed source moved when it is a bare
                # Name (variadic.py::_bloom_move_array). A struct field / call / index
                # source would be consumed by the callee yet still freed by the
                # caller's RAII -> double free. Confine the source to a plain variable.
                er.emit(validator.reporter, er.ERR.CE0120, arg.loc,
                        message="bloom source must be a bare array variable, "
                                "not an arbitrary expression")
            validator.validate_expression(arg)
            if array_ty is not None:
                arg_type = validator.infer_expression_type(arg)
                if arg_type is not None and not types_compatible(validator, arg_type, array_ty):
                    er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                            index=index, expected=display_type(array_ty), got=display_type(arg_type))
        else:
            propagate_types_to_value(validator, arg, element_ty)
            validator.validate_expression(arg)
            if element_ty is not None:
                arg_type = validator.infer_expression_type(arg)
                if arg_type is not None and not types_compatible(validator, arg_type, element_ty):
                    er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                            index=index, expected=display_type(element_ty), got=display_type(arg_type))


def validate_fn_value_call_args(validator: 'TypeValidator', args, fn_ty,
                                span) -> None:
    """Arity and per-argument check for a call through a function VALUE (CE2092)."""
    expected = fn_ty.param_types
    if len(args) != len(expected):
        er.emit(validator.reporter, er.ERR.CE2092, span,
                expected=display_type(fn_ty),
                actual=f"a call with {len(args)} argument(s)")
        return
    for arg, param_ty in zip(args, expected, strict=False):
        validator.validate_expression(arg)
        arg_ty = validator.infer_expression_type(arg)
        if arg_ty is None:
            continue
        if types_compatible(validator, arg_ty, param_ty):
            continue
        diag = er.emit_with(validator.reporter, er.ERR.CE2092, getattr(arg, 'loc', span),
                            expected=display_type(param_ty), actual=display_type(arg_ty))
        _explain_missing_borrow(diag, arg, arg_ty, param_ty)
        diag.emit()


def _explain_missing_borrow(diag, arg, arg_ty, param_ty) -> None:
    """Add the "you meant `peek x`" help where the mismatch is a missing borrow."""
    from sushi_lang.semantics.typesys import ReferenceType
    if not isinstance(arg, Name) or isinstance(arg_ty, ReferenceType):
        return
    if not isinstance(param_ty, ReferenceType) or param_ty.referenced_type != arg_ty:
        return
    diag.help(f"a borrow is created where it is USED, so the argument is written "
              f"`{param_ty.mutability} {arg.id}`; a reference-typed name mentioned "
              f"bare is its referent")


def validate_indirect_call(validator: 'TypeValidator', call: Call, fn_ty) -> None:
    """Validate a call through a first-class function value held in a local (CE2092)."""
    validate_fn_value_call_args(validator, call.args, fn_ty, call.callee.loc)


def validate_function_call(validator: 'TypeValidator', call: Call) -> None:
    """Validate function call arguments and types (CE2006, CE2008)."""
    from sushi_lang.semantics.ast import Name

    # Call-through an arbitrary expression that evaluates to a function value:
    # `env.f(x)` (a captured closure in a lifted lambda body), `obj.handler()`,
    # `arr[0]()`, `(e)()`. If the callee is a FunctionType, validate it as an indirect
    # call; otherwise the expression is not callable.
    from sushi_lang.semantics.typesys import FunctionType
    if not isinstance(call.callee, Name):
        # validate_expression, not infer_expression_type: the callee is a full expression
        # and needs validating in its own right, which is also what annotates it. Inferring
        # alone left a `??` callee (`fns.get(0)??(10)`) unannotated, so the backend fell
        # back to re-deriving its type.
        callee_ty = validator.validate_expression(call.callee)
        if isinstance(callee_ty, FunctionType):
            call.callee_fn_type = callee_ty  # backend reads this for the indirect call
            validate_indirect_call(validator, call, callee_ty)
        else:
            call.callee_unresolved = True
            er.emit(validator.reporter, er.ERR.CE2092, getattr(call.callee, 'loc', call.loc),
                    expected="a function value",
                    actual=display_type(callee_ty) if callee_ty is not None else "a non-function expression")
        return

    function_name = call.callee.id

    callee_var_ty = validator.variable_types.get(function_name)
    if isinstance(callee_var_ty, FunctionType):
        validate_indirect_call(validator, call, callee_var_ty)
        return

    # Section 8's ladder crosses the two function tables here: the asking unit's OWN
    # concrete declaration answers before a generic from next door does (#495), and a
    # generic resolves through the same per-unit ladder a concrete function walks.
    own_concrete = None
    if validator.current_unit_name is not None:
        own_concrete = validator.func_table.by_unit.get(
            validator.current_unit_name, {}).get(function_name)
    if own_concrete is None and validator.generic_sig(function_name) is not None:
        from .generics import validate_generic_function_call
        validate_generic_function_call(validator, call, function_name)
        return

    if function_name in validator.struct_table.by_name:
        from .structs import validate_struct_constructor
        validate_struct_constructor(validator, call)
        return

    func_sig = validator.func_sig(function_name)

    # Section 8's ladder: a declaration wins over a name a flat `use` brought in, and a
    # registry stdlib module is a flat import. Asking the standard library first was
    # what made `use <math>` beside a unit's own `sin` crash the compiler (section 1.3).
    if func_sig is None:
        stdlib_func = check_stdlib_function(validator, call)
        if stdlib_func is not None:
            validate_stdlib_function(validator, call, stdlib_func)
            return

    if func_sig is None:
        call.callee_unresolved = True
        # A name a library declares and keeps. It resolves to nothing here BECAUSE the
        # library kept it, so "undefined" was the wrong word for it (#469). The callee
        # stays unresolved either way: no signature travels with a kept name, so the
        # borrow pass must judge no argument against one.
        kept = validator.library_not_exported.get(function_name)
        if kept is not None and reject_private_kept_call(
                validator, function_name, call.callee.loc,
                library=kept[0], kind=kept[1]):
            return
        diag = er.emit_with(validator.reporter, er.ERR.CE2008, call.callee.loc,
                            name=function_name)
        # A generic struct constructor used inline is the most common cause; attach the
        # hint as a real help line on the diagnostic instead of a hand-indented print
        # faking a note underneath it.
        if function_name in validator.generic_struct_table.by_name:
            diag.help("generic struct constructors require explicit type parameters "
                      "in variable declarations")
        missing = out_of_scope_help(validator, "function", function_name)
        if missing is not None:
            diag.help(missing)
        diag.emit()
        return

    if reject_private_call(validator, "function", func_sig, call.callee.loc):
        return

    # More than one unit in scope declares this name and nothing here says which is
    # meant (CE3012). The unit's OWN declaration wins outright, so this only fires on
    # a name that comes from somewhere else entirely.
    if reject_ambiguous_name(validator, "function", function_name, call.callee.loc):
        return

    # A unit that declared this name and lost it may STILL be reading somebody else's
    # signature: only the concrete table carries a per-unit view, so a displaced GENERIC
    # declaration has none. Measuring the unit's own call against the winner's signature
    # is the D2 cascade -- the arity and every argument reported against a declaration
    # this unit never wrote -- so the arguments are validated in their own right and
    # nothing else is. Where the per-unit view did answer, the unit reads its own and
    # every ordinary rule applies.
    if (name_is_contested(validator, "function", function_name)
            and getattr(func_sig, "unit_name", None) != validator.current_unit_name):
        for arg in call.args:
            validator.validate_expression(arg)
        return

    validate_call_arguments(validator, function_name, func_sig, call.args,
                            call.callee.loc)


def validate_call_arguments(validator: 'TypeValidator', function_name: str, func_sig,
                            actual_args: list, loc) -> None:
    """Check a named callee's arguments against its declared parameters.

    Split out of `validate_function_call` so a call written through a namespace
    (`geo.twice(42)`) is measured against exactly the same rules as the bare form. The
    call node differs -- a `DotCall` carries no `callee` -- and nothing else does.
    """
    expected_params = func_sig.params

    # Native variadic call: a trailing '...T' parameter collects all remaining
    # trailing arguments into a T[]. Validate the fixed prefix as usual, then
    # validate each trailing argument against the element type T.
    variadic_param = (
        expected_params[-1]
        if expected_params and getattr(expected_params[-1], "is_variadic", False)
        else None
    )
    if variadic_param is not None:
        from sushi_lang.semantics.typesys import DynamicArrayType
        fixed_count = len(expected_params) - 1

        check_arguments(validator, function_name,
                        [p.ty for p in expected_params[:fixed_count]],
                        actual_args, loc,
                        mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                        minimum_arity=True)

        # Validate trailing variadic arguments. Two forms are accepted:
        #   - individual values, each type-checked against element type T;
        #   - a single bloom spread `arr...`, type-checked against the whole array T[].
        element_ty = (
            variadic_param.ty.base_type
            if isinstance(variadic_param.ty, DynamicArrayType)
            else variadic_param.ty
        )
        validate_variadic_trailing_args(
            validator, actual_args[fixed_count:], fixed_count,
            variadic_param.ty, element_ty)
        return

    check_arguments(validator, function_name, [p.ty for p in expected_params],
                    actual_args, loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009)


def check_stdlib_function(validator: 'TypeValidator',
                          call: Call) -> Optional[Tuple[str, Any]]:
    """The registry stdlib function a bare name reaches, with the module that has it."""
    return validator.func_table.lookup_stdlib_by_name(written_callee(call)[0],
                                                      validator.scope)


def written_callee(call) -> tuple:
    """The callee's name and the span that names it, for a bare or a qualified call.

    A qualified call is a `DotCall` and carries no `callee`: the method IS the name and
    the node IS the span. One reader, so a stdlib rule does not have to know which of
    the two shapes reached it.
    """
    callee = getattr(call, "callee", None)
    if callee is not None:
        return callee.id, callee.loc
    return call.method, call.loc


def validate_stdlib_function(validator: 'TypeValidator', call: Call, module_and_func: tuple) -> None:
    """Validate a stdlib function call (arg count and types)."""
    from sushi_lang.semantics.typesys import DynamicArrayType
    module_path, stdlib_func = module_and_func
    function_name, callee_loc = written_callee(call)
    args = call.args if hasattr(call, 'args') else []

    if stdlib_func.params is None:
        for arg in args:
            validator.validate_expression(arg)
        _validate_polymorphic_math(validator, call, function_name)
        return

    expected_params = stdlib_func.params

    # Native variadic stdlib call (e.g. run): the last param is a collecting '...T'.
    # Validate the fixed prefix, then the trailing args (individual values or a bloom)
    # via the shared variadic-trailing policy. A bloom `arr...` is Spread-aware here,
    # so it must be handled BEFORE any generic per-arg validation.
    if getattr(stdlib_func, "is_variadic", False):
        fixed_count = len(expected_params) - 1
        if not check_arguments(validator, function_name,
                               expected_params[:fixed_count], args, callee_loc,
                               mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                               minimum_arity=True, stop_on_arity=True):
            return
        array_ty = expected_params[-1]
        element_ty = array_ty.base_type if isinstance(array_ty, DynamicArrayType) else array_ty
        validate_variadic_trailing_args(
            validator, args[fixed_count:], fixed_count, array_ty, element_ty)
        return

    check_arguments(validator, function_name, expected_params, args, callee_loc,
                    mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009)


def _validate_polymorphic_math(validator: 'TypeValidator', call: Call, function_name: str) -> None:
    """Validate polymorphic math functions (abs, min, max)."""
    args = call.args if hasattr(call, 'args') else []
    _name, callee_loc = written_callee(call)

    # `abs` is the one caller that reads a SIGNED set, and `type_predicates` holds no
    # such name today, so this one stays local.
    SIGNED_INTS = BUILTIN_INTEGER_TYPES - BUILTIN_UNSIGNED_INTEGER_TYPES
    FLOATS = BUILTIN_FLOAT_TYPES
    NUMERIC = BUILTIN_NUMERIC_TYPES

    if function_name == "abs":
        if len(args) != 1:
            er.emit(validator.reporter, er.ERR.CE2009, callee_loc,
                   name="abs", expected=1, got=len(args))
            return
        arg_type = validator.infer_expression_type(args[0])
        if arg_type is not None and arg_type not in (SIGNED_INTS | FLOATS):
            er.emit(validator.reporter, er.ERR.CE2006, args[0].loc,
                   index=1, expected="signed integer or float", got=display_type(arg_type))

    elif function_name in ("min", "max"):
        if len(args) != 2:
            er.emit(validator.reporter, er.ERR.CE2009, callee_loc,
                   name=function_name, expected=2, got=len(args))
            return
        type_a = validator.infer_expression_type(args[0])
        type_b = validator.infer_expression_type(args[1])
        if type_a is not None and type_a not in NUMERIC:
            er.emit(validator.reporter, er.ERR.CE2006, args[0].loc,
                   index=1, expected="numeric type", got=display_type(type_a))
        if type_b is not None and type_b not in NUMERIC:
            er.emit(validator.reporter, er.ERR.CE2006, args[1].loc,
                   index=2, expected="numeric type", got=display_type(type_b))
        if type_a is not None and type_b is not None and type_a != type_b:
            er.emit(validator.reporter, er.ERR.CE2006, args[1].loc,
                   index=2, expected=display_type(type_a), got=display_type(type_b))
