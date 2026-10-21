"""User-defined and stdlib function call validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.ast import Call, Name, Spread
from sushi_lang.semantics.ffi_boundary import ERRNO_FUNCTION, unit_declares_external_block
from sushi_lang.semantics.name_ladder import call_constructs_struct
from ..arguments import check_arguments
from ..compatibility import types_compatible
from ..propagation import propagate_types_to_value, type_literal_from_sibling

if TYPE_CHECKING:
    from .. import TypeValidator


def own_concrete_function(func_table, name: str, unit: Optional[str]):
    """The concrete function the unit itself declares under a bare name, or None.

    Section 8's ladder crosses the two function tables here: the asking unit's OWN
    concrete declaration answers a bare name before a generic an import brought, in
    every position -- a call and a function value alike (#495, #963, #1003). The
    typecheck pass and the instantiate pass both ask this, so the two cannot drift.
    """
    if func_table is None or unit is None:
        return None
    return func_table.declared(name, unit)


def struct_takes_the_call(validator: 'TypeValidator', name: str) -> bool:
    """Whether a bare call `name(...)` constructs the struct of that name here."""
    return call_constructs_struct(name, validator.struct_table.by_name,
                                  validator.func_table, validator.current_unit_name)


def generic_struct_takes_the_call(validator: 'TypeValidator', name: str) -> bool:
    """Whether a bare call `name(...)` constructs an instance of the generic struct `name`.

    The same ladder as the concrete struct: the unit's own function of that name wins.
    """
    return call_constructs_struct(name, validator.generic_struct_table.by_name,
                                  validator.func_table, validator.current_unit_name)


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


def validate_errno_call(validator: 'TypeValidator', call: Call) -> None:
    """`errno()`: no argument, and a unit that declares an `unsafe external` block."""
    if not check_arguments(validator, ERRNO_FUNCTION, (), call.args, call.callee.loc,
                           mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                           stop_on_arity=True):
        return
    if not unit_declares_external_block(validator.external_table,
                                        validator.current_unit_name):
        er.emit(validator.reporter, er.ERR.CE5014, call.callee.loc)


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
        _validate_math_family(validator, call, function_name)
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


def _validate_math_family(validator: 'TypeValidator', call: Call, function_name: str) -> None:
    """A `<math>` family call (`abs`, `min`, `max`, `divmod`), measured against its table.

    The count comes first. Then each argument must have a type of the family, and the two
    arguments of a two-argument family must have one type.
    """
    from sushi_lang.sushi_stdlib.src.math import FAMILY_TYPE_NAMES, MATH_FAMILIES

    args = call.args if hasattr(call, 'args') else []
    _name, callee_loc = written_callee(call)
    arity, family_types = MATH_FAMILIES[function_name]

    if arity == 2 and len(args) == 2:
        type_literal_from_sibling(validator, args[0], args[1],
                                  validator.infer_expression_type)
    for arg in args:
        validator.validate_expression(arg)

    if len(args) != arity:
        er.emit(validator.reporter, er.ERR.CE2009, callee_loc,
               name=function_name, expected=arity, got=len(args))
        return
    arg_types = [validator.infer_expression_type(arg) for arg in args]
    for index, (arg, arg_type) in enumerate(zip(args, arg_types, strict=True), start=1):
        if arg_type is not None and arg_type not in family_types:
            er.emit(validator.reporter, er.ERR.CE2006, arg.loc, index=index,
                   expected=FAMILY_TYPE_NAMES[function_name], got=display_type(arg_type))
    if arity == 2 and None not in arg_types and arg_types[0] != arg_types[1]:
        er.emit(validator.reporter, er.ERR.CE2006, args[1].loc, index=2,
               expected=display_type(arg_types[0]), got=display_type(arg_types[1]))
