# semantics/passes/types/calls/user_defined.py
"""
User-defined and stdlib function call validation.

Handles validation for:
- User-defined function calls
- Stdlib function calls (time, io, etc.)
- Built-in global functions (open)
- Cross-unit function visibility
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, StructType
from sushi_lang.semantics.ast import Call, Name, Spread
from ..compatibility import types_compatible
from ..utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall

if TYPE_CHECKING:
    from .. import TypeValidator


def _reject_misplaced_spread(validator: 'TypeValidator', arg) -> bool:
    """Emit CE0120 if `arg` is a bloom spread `arr...` in a position where one is not
    allowed (a non-variadic call, or a fixed/non-last argument). Still validates the
    inner expression so downstream inference does not crash. Returns True if rejected.
    """
    if isinstance(arg, Spread):
        er.emit(validator.reporter, er.ERR.CE0120, arg.loc,
                message="bloom argument 'arr...' is only allowed as the last argument "
                        "of a call to a variadic '...T' function")
        validator.validate_expression(arg)
        return True
    return False


def validate_variadic_trailing_args(validator: 'TypeValidator', trailing: list,
                                    fixed_count: int, array_ty, element_ty) -> None:
    """Validate the trailing arguments of a variadic call (native '...T' or stdlib).

    Two accepted forms:
      - individual values, each checked against the element type T;
      - a single bloom spread `arr...`, checked against the whole array type T[].
    A spread that is not the sole trailing argument is CE0120; a type mismatch is CE2006.
    Shared by user-function and stdlib (run) variadic validation.
    """
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
                            index=index, expected=str(array_ty), got=str(arg_type))
        else:
            propagate_enum_type_to_dotcall(validator, arg, element_ty)
            propagate_struct_type_to_dotcall(validator, arg, element_ty)
            validator.validate_expression(arg)
            if element_ty is not None:
                arg_type = validator.infer_expression_type(arg)
                if arg_type is not None and not types_compatible(validator, arg_type, element_ty):
                    er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                            index=index, expected=str(element_ty), got=str(arg_type))


def validate_indirect_call(validator: 'TypeValidator', call: Call, fn_ty) -> None:
    """Validate a call through a first-class function value (CE2092).

    The callee is a local variable of FunctionType. Check arity and each argument
    against the function type's parameter types (invariant).
    """
    expected = fn_ty.param_types
    actual = call.args
    if len(actual) != len(expected):
        er.emit(validator.reporter, er.ERR.CE2092, call.callee.loc,
                expected=str(fn_ty),
                actual=f"a call with {len(actual)} argument(s)")
        return
    for arg, param_ty in zip(actual, expected, strict=False):
        validator.validate_expression(arg)
        arg_ty = validator.infer_expression_type(arg)
        if arg_ty is None:
            continue
        if not types_compatible(validator, arg_ty, param_ty):
            er.emit(validator.reporter, er.ERR.CE2092, getattr(arg, 'loc', call.callee.loc),
                    expected=str(param_ty), actual=str(arg_ty))


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
            er.emit(validator.reporter, er.ERR.CE2092, getattr(call.callee, 'loc', call.loc),
                    expected="a function value",
                    actual=str(callee_ty) if callee_ty is not None else "a non-function expression")
        return

    # Check if function exists
    function_name = call.callee.id

    # Indirect call through a first-class function value held in a local variable.
    # A local shadows any same-named top-level function, so this is checked first.
    callee_var_ty = validator.variable_types.get(function_name)
    if isinstance(callee_var_ty, FunctionType):
        validate_indirect_call(validator, call, callee_var_ty)
        return

    # Check if this is a generic function call (handled by generics module)
    if function_name in validator.generic_func_table.by_name:
        from .generics import validate_generic_function_call
        validate_generic_function_call(validator, call, function_name)
        return

    # Check if this is a struct constructor instead of a function call (handled by structs module)
    if function_name in validator.struct_table.by_name:
        from .structs import validate_struct_constructor
        validate_struct_constructor(validator, call)
        return

    # Check for built-in global functions
    if function_name == "open":
        validate_open_function(validator, call)
        return

    # Check if this is a stdlib function call
    # Stdlib functions are registered during Pass 0 in FunctionTable
    stdlib_func = check_stdlib_function(validator, call)
    if stdlib_func is not None:
        # Stdlib function found - validate using its registered validator
        validate_stdlib_function(validator, call, stdlib_func)
        return

    if function_name not in validator.func_table.by_name:
        diag = er.emit_with(validator.reporter, er.ERR.CE2008, call.callee.loc,
                            name=function_name)
        # A generic struct constructor used inline is the most common cause; attach the
        # hint as a real help line on the diagnostic instead of a hand-indented print
        # faking a note underneath it.
        if function_name in validator.generic_struct_table.by_name:
            diag.help("generic struct constructors require explicit type parameters "
                      "in variable declarations")
        diag.emit()
        return

    # Get function signature
    func_sig = validator.func_table.by_name[function_name]

    # Check function visibility for cross-unit calls (multi-file compilation only)
    if (validator.current_unit_name is not None and
        func_sig.unit_name is not None and
        func_sig.unit_name != validator.current_unit_name):
        # This is a cross-unit function call - check if function is public
        if not func_sig.is_public:
            er.emit(validator.reporter, er.ERR.CE3005, call.callee.loc,
                   name=function_name,
                   current_unit=validator.current_unit_name,
                   func_unit=func_sig.unit_name)
            return

    expected_params = func_sig.params
    actual_args = call.args

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

        if len(actual_args) < fixed_count:
            er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
                   name=function_name, expected=fixed_count, got=len(actual_args))

        # Validate fixed (non-variadic) arguments. A bloom spread is illegal here.
        for i, (arg, param) in enumerate(zip(actual_args[:fixed_count], expected_params[:fixed_count], strict=False)):
            if _reject_misplaced_spread(validator, arg):
                continue
            propagate_enum_type_to_dotcall(validator, arg, param.ty)
            propagate_struct_type_to_dotcall(validator, arg, param.ty)
            validator.validate_expression(arg)
            if param.ty is not None:
                arg_type = validator.infer_expression_type(arg)
                if arg_type is not None and not types_compatible(validator, arg_type, param.ty):
                    er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                           index=i + 1, expected=str(param.ty), got=str(arg_type))

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

    # Check argument count
    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=function_name, expected=len(expected_params), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding parameter type
    for i, (arg, param) in enumerate(zip(actual_args, expected_params, strict=False)):
        # A bloom spread `arr...` is illegal in a call to a non-variadic function.
        if _reject_misplaced_spread(validator, arg):
            continue
        # Propagate expected types to DotCall nodes for generic enums (before validation)
        # This allows Maybe.None(), Result.Ok(), etc. to work as function arguments
        propagate_enum_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to DotCall nodes for generic structs (before validation)
        # This allows Own.alloc(42) to work as function arguments
        propagate_struct_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to Call nodes for generic struct constructors
        # This allows Box(42) to work when parameter expects Box<i32>
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(param.ty, StructType):
            struct_name = arg.callee.id
            # Check if this is a generic struct constructor
            if struct_name in validator.generic_struct_table.by_name:
                # Update the Call node's callee id to use the concrete type name
                arg.callee.id = param.ty.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        if param.ty is not None:  # Skip if parameter has unknown type
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, param.ty):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=str(param.ty), got=str(arg_type))

    # Validate any excess arguments (if more args than params)
    for i in range(len(expected_params), len(actual_args)):
        if _reject_misplaced_spread(validator, actual_args[i]):
            continue
        validator.validate_expression(actual_args[i])


def validate_open_function(validator: 'TypeValidator', call: Call) -> None:
    """Validate open() built-in function call.

    Signature: open(string path, FileMode mode) FileResult
    Returns: FileResult enum (Ok(file) or Err())
    """
    actual_args = call.args

    # Check argument count (must be exactly 2)
    if len(actual_args) != 2:
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name="open", expected=2, got=len(actual_args))
        return

    # Validate first argument: path (must be string)
    validator.validate_expression(actual_args[0])
    path_type = validator.infer_expression_type(actual_args[0])
    if path_type is not None and path_type != BuiltinType.STRING:
        er.emit(validator.reporter, er.ERR.CE2006, actual_args[0].loc,
               index=1, expected="string", got=str(path_type))

    # Validate second argument: mode (must be FileMode enum variant)
    validator.validate_expression(actual_args[1])
    mode_type = validator.infer_expression_type(actual_args[1])

    # Check if it's the FileMode enum type
    file_mode_enum = validator.enum_table.by_name.get("FileMode")
    if file_mode_enum is None:
        # FileMode enum not registered - this shouldn't happen
        return

    if mode_type is not None and mode_type != file_mode_enum:
        er.emit(validator.reporter, er.ERR.CE2006, actual_args[1].loc,
               index=2, expected="FileMode", got=str(mode_type))


def check_stdlib_function(validator: 'TypeValidator', call: Call) -> Optional[any]:
    """
    Check if a function call is to a stdlib function.

    Looks up the function in the FunctionTable's stdlib function registry.

    Args:
        validator: Type validator instance
        call: Function call AST node

    Returns:
        Tuple of (module_path, StdlibFunction) if found, None otherwise
    """
    function_name = call.callee.id

    # Try common module paths to find the function
    possible_modules = ["time", "sys/env", "sys/process", "math", "random", "io/files"]

    for module_path in possible_modules:
        stdlib_func = validator.func_table.lookup_stdlib_function(module_path, function_name)
        if stdlib_func is not None:
            return (module_path, stdlib_func)

    return None


def validate_stdlib_function(validator: 'TypeValidator', call: Call, module_and_func: tuple) -> None:
    """Validate a stdlib function call (arg count and types)."""
    from sushi_lang.semantics.typesys import DynamicArrayType
    module_path, stdlib_func = module_and_func
    function_name = call.callee.id
    args = call.args if hasattr(call, 'args') else []

    # Polymorphic functions need special handling
    if stdlib_func.params is None:
        # Validate all argument expressions first
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
        if len(args) < fixed_count:
            er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
                   name=function_name, expected=fixed_count, got=len(args))
            return
        for i, (arg, expected_type) in enumerate(zip(args[:fixed_count], expected_params[:fixed_count], strict=False)):
            if _reject_misplaced_spread(validator, arg):
                continue
            validator.validate_expression(arg)
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, expected_type):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=str(expected_type), got=str(arg_type))
        array_ty = expected_params[-1]
        element_ty = array_ty.base_type if isinstance(array_ty, DynamicArrayType) else array_ty
        validate_variadic_trailing_args(
            validator, args[fixed_count:], fixed_count, array_ty, element_ty)
        return

    # Validate all argument expressions first
    for arg in args:
        validator.validate_expression(arg)

    # Check argument count
    if len(args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=function_name, expected=len(expected_params), got=len(args))
        return

    # Check each argument type
    for i, (arg, expected_type) in enumerate(zip(args, expected_params, strict=False)):
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, expected_type):
            er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                   index=i+1, expected=str(expected_type), got=str(arg_type))


def _validate_polymorphic_math(validator: 'TypeValidator', call: Call, function_name: str) -> None:
    """Validate polymorphic math functions (abs, min, max)."""
    args = call.args if hasattr(call, 'args') else []

    SIGNED_INTS = {BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64}
    ALL_INTS = SIGNED_INTS | {BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64}
    FLOATS = {BuiltinType.F32, BuiltinType.F64}
    NUMERIC = ALL_INTS | FLOATS

    if function_name == "abs":
        if len(args) != 1:
            er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
                   name="abs", expected=1, got=len(args))
            return
        arg_type = validator.infer_expression_type(args[0])
        if arg_type is not None and arg_type not in (SIGNED_INTS | FLOATS):
            er.emit(validator.reporter, er.ERR.CE2006, args[0].loc,
                   index=1, expected="signed integer or float", got=str(arg_type))

    elif function_name in ("min", "max"):
        if len(args) != 2:
            er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
                   name=function_name, expected=2, got=len(args))
            return
        type_a = validator.infer_expression_type(args[0])
        type_b = validator.infer_expression_type(args[1])
        if type_a is not None and type_a not in NUMERIC:
            er.emit(validator.reporter, er.ERR.CE2006, args[0].loc,
                   index=1, expected="numeric type", got=str(type_a))
        if type_b is not None and type_b not in NUMERIC:
            er.emit(validator.reporter, er.ERR.CE2006, args[1].loc,
                   index=2, expected="numeric type", got=str(type_b))
        if type_a is not None and type_b is not None and type_a != type_b:
            er.emit(validator.reporter, er.ERR.CE2006, args[1].loc,
                   index=2, expected=str(type_a), got=str(type_b))
