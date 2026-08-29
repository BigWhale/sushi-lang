"""Standard library time function call emission."""
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
    """Emit a call to a time module function."""
    require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)

    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # All time functions return i32 (0 on success, remaining microseconds if interrupted)
    # But they're wrapped in Result<i32> at the semantic level
    # The actual LLVM functions return bare i32

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

    # The stdlib functions return bare i32, but Sushi functions return Result<i32, StdError>
    # We need to wrap the result in a Result.Ok() enum
    # Result<i32, StdError> enum layout: {i32 tag, [N x i8] data}

    from sushi_lang.semantics.typesys import UnknownType
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table

    is_i64 = func_name in ["now", "monotonic_ns"]
    ok_type = BuiltinType.I64 if is_i64 else BuiltinType.I32
    err_type = UnknownType("StdError")
    result_enum = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type, struct_table=codegen.struct_table.by_name)

    if result_enum:
        result_llvm_type = codegen.types.ll_type(result_enum)
        ok_variant_index = result_enum.get_variant_index("Ok")

        ok_result = ir.Constant(result_llvm_type, ir.Undefined)
        tag = ir.Constant(codegen.types.i32, ok_variant_index)
        ok_result = codegen.builder.insert_value(ok_result, tag, 0, name="ok_tag")

        data_array_type = result_llvm_type.elements[1]

        value_alloca = codegen.builder.alloca(i64 if is_i64 else i32, name="time_result_value")
        codegen.builder.store(result, value_alloca)

        data_alloca = codegen.builder.alloca(data_array_type, name="data_array")

        src_ptr = codegen.builder.bitcast(value_alloca, codegen.types.i8.as_pointer())
        dest_ptr = codegen.builder.bitcast(data_alloca, codegen.types.i8.as_pointer())

        # Copy the ok value into the data array (4 or 8 bytes). i64-length
        # llvm.memcpy so the length register is never fed a value with garbage
        # upper bits (#149/#151).
        size_const = ir.Constant(codegen.types.i64, 8 if is_i64 else 4)
        memcpy_fn = codegen.module.declare_intrinsic('llvm.memcpy', [
            ir.PointerType(codegen.types.i8),
            ir.PointerType(codegen.types.i8),
            codegen.types.i64
        ])
        is_volatile = FALSE_I1
        codegen.builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

        data_value = codegen.builder.load(data_alloca, name="data_value")
        ok_result = codegen.builder.insert_value(ok_result, data_value, 1, name="ok_result")

        return codegen.utils.as_i1(ok_result) if to_i1 else ok_result
    else:
        raise_internal_error("CE0091", type="Result<i64>" if is_i64 else "Result<i32>")
