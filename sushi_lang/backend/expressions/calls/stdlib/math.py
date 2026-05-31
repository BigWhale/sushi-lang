"""
Standard library math function call emission.

This module handles external calls to precompiled stdlib math functions
(abs, min, max, sqrt, pow, trigonometric, logarithmic, etc.).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_math_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a math module function.

    This function emits an external call to a precompiled stdlib math function.
    Maps user-facing function names (abs, min, max, sqrt, etc.) to their internal
    sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('abs', 'min', 'max', 'sqrt', 'pow', etc.)
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the function is not a recognized math function
    """
    builder = require_builder(codegen)

    from sushi_lang.backend.llvm_functions import declare_stdlib_function

    # Get argument values
    args = [codegen.expressions.emit_expr(arg) for arg in expr.args]

    # Determine the type-specific function name for polymorphic functions (abs, min, max)
    if func_name in {'abs', 'min', 'max'} and args:
        # These are polymorphic - need to determine the type suffix
        arg_type = args[0].type
        type_suffix = _get_math_type_suffix(arg_type)
        stdlib_func_name = f"sushi_{func_name}_{type_suffix}"

        # Declare the function with the appropriate signature
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, arg_type, [arg_type] * len(args))
    elif func_name in {
        'sqrt', 'floor', 'ceil', 'round', 'trunc',
        'sin', 'cos', 'tan',
        'asin', 'acos', 'atan',
        'sinh', 'cosh', 'tanh',
        'log', 'log2', 'log10',
        'exp', 'exp2',
    }:
        # These take f64 and return f64
        f64 = ir.DoubleType()
        stdlib_func_name = f"sushi_{func_name}"
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [f64])
        # Convert args to f64 if needed
        if args and args[0].type != f64:
            from sushi_lang.backend.expressions.casts import cast_int_to_float, cast_float_to_float
            if isinstance(args[0].type, ir.IntType):
                args[0] = cast_int_to_float(codegen, args[0], f64)
            else:
                args[0] = cast_float_to_float(codegen, args[0], f64)
    elif func_name in {'pow', 'atan2', 'hypot'}:
        # These take two f64 arguments and return f64
        f64 = ir.DoubleType()
        stdlib_func_name = f"sushi_{func_name}"
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [f64, f64])
        # Convert both args to f64 if needed
        from sushi_lang.backend.expressions.casts import cast_int_to_float, cast_float_to_float
        for i in range(len(args)):
            if args[i].type != f64:
                if isinstance(args[i].type, ir.IntType):
                    args[i] = cast_int_to_float(codegen, args[i], f64)
                else:
                    args[i] = cast_float_to_float(codegen, args[i], f64)
    else:
        raise ValueError(f"Unknown math function: {func_name}")

    # Call the function
    result = codegen.builder.call(stdlib_func, args, name=f"{func_name}_result")
    return codegen.utils.as_i1(result) if to_i1 else result


def _get_math_type_suffix(llvm_type) -> str:
    """Get the type suffix for polymorphic math functions.

    Args:
        llvm_type: The LLVM IR type

    Returns:
        Type suffix string (e.g., 'i32', 'f64', 'u8')

    Note:
        We cannot distinguish between signed and unsigned integers in LLVM IR alone.
        Both i32 and u32 are represented as ir.IntType(32).
        For now, we default to signed types. If we need unsigned support for min/max,
        we would need to pass semantic type information through.
    """
    from llvmlite import ir

    if isinstance(llvm_type, ir.IntType):
        bit_width = llvm_type.width
        if bit_width == 8:
            return 'i8'
        elif bit_width == 16:
            return 'i16'
        elif bit_width == 32:
            return 'i32'
        elif bit_width == 64:
            return 'i64'
    elif isinstance(llvm_type, ir.FloatType):
        return 'f32'
    elif isinstance(llvm_type, ir.DoubleType):
        return 'f64'

    raise ValueError(f"Unsupported type for math function: {llvm_type}")
