"""
Standard library sys/env function call emission.

This module handles external calls to precompiled stdlib env functions
(getenv, setenv).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, FAT_POINTER_SIZE_BYTES
from sushi_lang.backend.constants.llvm_values import FALSE_I1
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_env_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a sys/env module function.

    This function emits an external call to a precompiled stdlib env function.
    Maps user-facing function names (getenv, setenv) to their internal
    sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('getenv' or 'setenv')
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call (Maybe<string> or Result<i32>)

    Raises:
        ValueError: If the function is not a recognized env function
    """
    builder = require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = ir.IntType(8).as_pointer()

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # String type: {i8* data, i32 size}
    string_type = codegen.types.ll_type(BuiltinType.STRING)

    if func_name == "getenv":
        # getenv(string key) -> Maybe<string>
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method="getenv", expected=1, got=len(expr.args))

        key_value = codegen.expressions.emit_expr(expr.args[0])

        # Maybe<string> type: {i32 tag, [12 x i8] data}
        maybe_string_data_size = FAT_POINTER_SIZE_BYTES
        maybe_string_type = ir.LiteralStructType([i32, ir.ArrayType(ir.IntType(8), maybe_string_data_size)])

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, maybe_string_type, [string_type])
        result = codegen.builder.call(stdlib_func, [key_value], name="getenv_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "setenv":
        # setenv(string key, string value) -> Result<i32>
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method="setenv", expected=2, got=len(expr.args))

        key_value = codegen.expressions.emit_expr(expr.args[0])
        value_value = codegen.expressions.emit_expr(expr.args[1])

        # The stdlib function returns bare i32
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [string_type, string_type])
        result = codegen.builder.call(stdlib_func, [key_value, value_value], name="setenv_result")

        # Wrap in Result.Ok() enum (same as time functions)
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.backend.generics.results import ensure_result_type_in_table

        # Create Result<i32, EnvError> enum if it doesn't exist
        ok_type = BuiltinType.I32
        err_type = UnknownType("EnvError")
        result_enum = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type)

        if result_enum:
            result_llvm_type = codegen.types.ll_type(result_enum)
            ok_variant_index = result_enum.get_variant_index("Ok")

            # Create Result.Ok(value) enum
            ok_result = ir.Constant(result_llvm_type, ir.Undefined)
            tag = ir.Constant(codegen.types.i32, ok_variant_index)
            ok_result = codegen.builder.insert_value(ok_result, tag, 0, name="ok_tag")

            # Pack the i32 value into the data array
            data_array_type = result_llvm_type.elements[1]

            # Allocate space for the value and data array
            value_alloca = codegen.builder.alloca(i32, name="setenv_result_value")
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

    else:
        raise_internal_error("CE0024", type="sys/env", method=func_name)
