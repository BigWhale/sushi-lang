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
from sushi_lang.semantics.ast import Call
from ..compatibility import types_compatible
from ..utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall

if TYPE_CHECKING:
    from .. import TypeValidator


def validate_function_call(validator: 'TypeValidator', call: Call) -> None:
    """Validate function call arguments and types (CE2006, CE2008)."""
    # Check if function exists
    function_name = call.callee.id

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
        er.emit(validator.reporter, er.ERR.CE2008, call.callee.loc, name=function_name)
        # Check if this is a generic struct constructor used inline - provide a helpful hint
        if function_name in validator.generic_struct_table.by_name:
            import sys
            print(f"      Generic struct constructors require explicit type parameters in variable declarations", file=sys.stderr)
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

    # Check argument count
    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=function_name, expected=len(expected_params), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding parameter type
    for i, (arg, param) in enumerate(zip(actual_args, expected_params)):
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
    module_path, stdlib_func = module_and_func
    function_name = call.callee.id
    args = call.args if hasattr(call, 'args') else []

    # Validate all argument expressions first
    for arg in args:
        validator.validate_expression(arg)

    # Polymorphic functions need special handling
    if stdlib_func.params is None:
        _validate_polymorphic_math(validator, call, function_name)
        return

    expected_params = stdlib_func.params

    # Check argument count
    if len(args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=function_name, expected=len(expected_params), got=len(args))
        return

    # Check each argument type
    for i, (arg, expected_type) in enumerate(zip(args, expected_params)):
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
