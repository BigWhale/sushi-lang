"""Standard library time function call emission."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder
from sushi_lang.backend.expressions.calls.stdlib.status_result import ok_result, status_result

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_time_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a time module function."""
    require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)

    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    if func_name in ["sleep", "msleep", "usleep"]:
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))

        arg_value = codegen.expressions.emit_expr(expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i64])
        result = codegen.builder.call(stdlib_func, [arg_value], name=f"{func_name}_result")

    elif func_name == "nanosleep":
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method="nanosleep", expected=2, got=len(expr.args))

        seconds_value = codegen.expressions.emit_expr(expr.args[0])
        nanoseconds_value = codegen.expressions.emit_expr(expr.args[1])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i64, i64])
        result = codegen.builder.call(stdlib_func, [seconds_value, nanoseconds_value], name="nanosleep_result")

    elif func_name in ["now", "monotonic_ns"]:
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method=func_name, expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i64, [])
        result = codegen.builder.call(stdlib_func, [], name=f"{func_name}_result")

    else:
        raise_internal_error("CE0024", type="time", method=func_name)

    if func_name in ["now", "monotonic_ns"]:
        value = ok_result(codegen, result, BuiltinType.I64, "StdError")
    else:
        value = status_result(codegen, result, BuiltinType.I32, "StdError", "Error")
    return codegen.utils.as_i1(value) if to_i1 else value
