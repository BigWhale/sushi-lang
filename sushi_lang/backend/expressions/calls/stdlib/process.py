"""
Standard library sys/process function call emission.

This module handles external calls to precompiled stdlib process functions
(getcwd, chdir, exit, getpid, getuid).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, FAT_POINTER_SIZE_BYTES
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_process_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a sys/process module function.

    This function emits an external call to a precompiled stdlib process function.
    Maps user-facing function names (getcwd, chdir, exit, getpid, getuid) to their
    internal sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('getcwd', 'chdir', 'exit', 'getpid', 'getuid')
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the function is not a recognized process function
    """
    builder = require_builder(codegen)

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = ir.IntType(8).as_pointer()
    void = ir.VoidType()

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_{func_name}"

    from sushi_lang.backend.llvm_functions import declare_stdlib_function

    # String type: {i8* data, i32 size}
    string_type = codegen.types.ll_type(BuiltinType.STRING)

    if func_name == "getcwd":
        # getcwd() -> Result<string, ProcessError>
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method="getcwd", expected=0, got=len(expr.args))

        # Result<string, ProcessError> type
        # string (fat pointer) = 12 bytes
        # ProcessError (unit enum) = 5 bytes
        # Result data size = max(12, 5) = 12 bytes
        result_string_data_size = FAT_POINTER_SIZE_BYTES  # 12 bytes
        result_string_type = ir.LiteralStructType([i32, ir.ArrayType(ir.IntType(8), result_string_data_size)])

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_string_type, [])
        result = codegen.builder.call(stdlib_func, [], name="getcwd_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "chdir":
        # chdir(string path) -> Result<i32, ProcessError>
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method="chdir", expected=1, got=len(expr.args))

        path_value = codegen.expressions.emit_expr(expr.args[0])

        # Result<i32, ProcessError> type
        # ProcessError is a unit enum: {i32 tag, [1 x i8] data} = 5 bytes
        # i32 = 4 bytes
        # Result data size = max(4, 5) = 5 bytes
        result_i32_type = ir.LiteralStructType([i32, ir.ArrayType(ir.IntType(8), 5)])

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_i32_type, [string_type])
        result = codegen.builder.call(stdlib_func, [path_value], name="chdir_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "exit":
        # exit(i32 code) -> ~
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method="exit", expected=1, got=len(expr.args))

        code_value = codegen.expressions.emit_expr(expr.args[0])

        # The stdlib function returns void
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, void, [i32])
        codegen.builder.call(stdlib_func, [code_value], name="exit_call")

        # exit() never returns, so emit unreachable
        codegen.builder.unreachable()

        # Return undef value (won't be used)
        return ir.Constant(i32, ir.Undefined)

    elif func_name == "getpid":
        # getpid() -> i32
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method="getpid", expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [])
        result = codegen.builder.call(stdlib_func, [], name="getpid_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "getuid":
        # getuid() -> i32
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method="getuid", expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [])
        result = codegen.builder.call(stdlib_func, [], name="getuid_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    else:
        raise_internal_error("CE0024", type="sys/process", method=func_name)
