"""
Standard library random function call emission.

This module handles external calls to precompiled stdlib random functions
(rand, rand_range, srand, rand_f64).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_random_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a random module function.

    This function emits an external call to a precompiled stdlib random function.
    Maps user-facing function names (rand, rand_range, srand, rand_f64) to their
    internal sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('rand', 'rand_range', 'srand', or 'rand_f64')
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the function is not a recognized random function
    """
    require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)
    f64 = ir.DoubleType()
    void = ir.VoidType()

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # Random functions return bare types (not Result<T>)
    # Result wrapping happens at semantic level

    if func_name == "rand":
        # rand() -> u64 (no parameters)
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method=func_name, expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i64, [])
        result = codegen.builder.call(stdlib_func, [], name="rand_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "rand_range":
        # rand_range(i32 min, i32 max) -> i32
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        min_value = codegen.expressions.emit_expr(expr.args[0])
        max_value = codegen.expressions.emit_expr(expr.args[1])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i32, i32])
        result = codegen.builder.call(stdlib_func, [min_value, max_value], name="rand_range_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "srand":
        # srand(u64 seed) -> ~ (void)
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))

        seed_value = codegen.expressions.emit_expr(expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, void, [i64])
        codegen.builder.call(stdlib_func, [seed_value])
        # Return blank (undef i32 value)
        return ir.Constant(i32, ir.Undefined)

    elif func_name == "rand_f64":
        # rand_f64() -> f64 (no parameters)
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method=func_name, expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [])
        result = codegen.builder.call(stdlib_func, [], name="rand_f64_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    else:
        raise_internal_error("CE0024", type="random", method=func_name)
