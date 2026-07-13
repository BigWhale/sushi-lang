"""
Standard library time function call emission.

This module handles external calls to precompiled stdlib time functions
(sleep, msleep, usleep, nanosleep).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import FALSE_I1
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_time_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a time module function.

    This function emits an external call to a precompiled stdlib time function.
    Maps user-facing function names (sleep, msleep, etc.) to their internal
    sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('sleep', 'msleep', 'usleep', or 'nanosleep')
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call (Result<i32> enum)

    Raises:
        ValueError: If the function is not a recognized time function
    """
    builder = require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # All time functions return i32 (0 on success, remaining microseconds if interrupted)
    # But they're wrapped in Result<i32> at the semantic level
    # The actual LLVM functions return bare i32

    if func_name in ["sleep", "msleep", "usleep"]:
        # These functions take one i64 argument
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))

        arg_value = codegen.expressions.emit_expr(expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i64])
        result = codegen.builder.call(stdlib_func, [arg_value], name=f"{func_name}_result")

    elif func_name == "nanosleep":
        # nanosleep takes two i64 arguments (seconds, nanoseconds)
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method="nanosleep", expected=2, got=len(expr.args))

        seconds_value = codegen.expressions.emit_expr(expr.args[0])
        nanoseconds_value = codegen.expressions.emit_expr(expr.args[1])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i64, i64])
        result = codegen.builder.call(stdlib_func, [seconds_value, nanoseconds_value], name="nanosleep_result")

    else:
        raise_internal_error("CE0024", type="time", method=func_name)

    # The stdlib functions return bare i32, but Sushi functions return Result<i32, StdError>
    # We need to wrap the result in a Result.Ok() enum
    # Result<i32, StdError> enum layout: {i32 tag, [N x i8] data}

    from sushi_lang.semantics.typesys import UnknownType
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table

    # Create Result<i32, StdError> enum if it doesn't exist
    ok_type = BuiltinType.I32
    err_type = UnknownType("StdError")
    result_enum = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type, struct_table=codegen.struct_table.by_name)

    if result_enum:
        result_llvm_type = codegen.types.ll_type(result_enum)
        ok_variant_index = result_enum.get_variant_index("Ok")

        # Create Result.Ok(value) enum
        ok_result = ir.Constant(result_llvm_type, ir.Undefined)
        tag = ir.Constant(codegen.types.i32, ok_variant_index)
        ok_result = codegen.builder.insert_value(ok_result, tag, 0, name="ok_tag")

        # Pack the i32 value into the data array
        # Get the data array type from the enum type
        data_array_type = result_llvm_type.elements[1]

        # Allocate space for the value and data array
        value_alloca = codegen.builder.alloca(i32, name="time_result_value")
        codegen.builder.store(result, value_alloca)

        data_alloca = codegen.builder.alloca(data_array_type, name="data_array")

        # Bitcast pointers to i8* for memcpy
        src_ptr = codegen.builder.bitcast(value_alloca, codegen.types.i8.as_pointer())
        dest_ptr = codegen.builder.bitcast(data_alloca, codegen.types.i8.as_pointer())

        # Copy i32 value into data array (4 bytes). i64-length llvm.memcpy so the
        # length register is never fed a value with garbage upper bits (#149/#151).
        size_const = ir.Constant(codegen.types.i64, 4)
        memcpy_fn = codegen.module.declare_intrinsic('llvm.memcpy', [
            ir.PointerType(codegen.types.i8),
            ir.PointerType(codegen.types.i8),
            codegen.types.i64
        ])
        is_volatile = FALSE_I1
        codegen.builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

        # Load the data array and insert into enum
        data_value = codegen.builder.load(data_alloca, name="data_value")
        ok_result = codegen.builder.insert_value(ok_result, data_value, 1, name="ok_result")

        return codegen.utils.as_i1(ok_result) if to_i1 else ok_result
    else:
        raise_internal_error("CE0091", type="Result<i32>")
