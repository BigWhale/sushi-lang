"""
Standard library I/O call emission (stdio, file, files).

This module handles external calls to precompiled stdlib functions for
stdin/stdout/stderr streams, FILE* operations, and io/files utilities.
These emitters are grouped together since they share C-runtime helpers.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_stdlib_stdio_call(
    codegen: 'LLVMCodegen',
    stream_name: str,
    method: str,
    args: list,
    to_i1: bool
) -> ir.Value:
    """Emit a call to a stdlib stdio method.

    This function emits an external call to a precompiled stdlib function
    for stdio operations (stdin, stdout, stderr).

    Args:
        codegen: The LLVM code generator
        stream_name: The stream name ('stdin', 'stdout', or 'stderr')
        method: The method name (e.g., "readln", "write", "read_bytes", "write_bytes")
        args: The method arguments
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the method is not implemented in stdlib
    """
    require_builder(codegen)
    # Map method names to stdlib function names
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    # Build function name: sushi_{stream}_{method}
    func_name = f"sushi_{stream_name}_{method}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # Handle each method type
    if stream_name == "stdin":
        if method == "readln":
            # {ptr, i32} @sushi_stdin_readln()
            # Returns fat pointer struct {i8*, i32}
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_readln_result")

        elif method == "read":
            # {ptr, i32} @sushi_stdin_read()
            # Returns fat pointer struct {i8*, i32}
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_read_result")

        elif method == "read_bytes":
            # {i32, i32, ptr} @sushi_stdin_read_bytes(i32 %count)
            # Returns array struct by value
            array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
            arg_value = codegen.expressions.emit_expr(args[0])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, array_struct_ty, [i32])
            result = codegen.builder.call(stdlib_func, [arg_value], name="stdin_read_bytes_result")

            # Store result in a slot and return pointer (to match variable storage conventions)
            result_slot = codegen.builder.alloca(array_struct_ty, name="stdin_read_bytes_slot")
            codegen.builder.store(result, result_slot)
            return result_slot

        elif method == "lines":
            # {i32, i32, {i8*, i32}*} @sushi_stdin_lines()
            # Returns iterator struct with fat pointer element type
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            iterator_struct_ty = ir.LiteralStructType([i32, i32, string_struct_ty.as_pointer()])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, iterator_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_lines_result")

    elif stream_name in ["stdout", "stderr"]:
        if method == "write":
            # i32 @sushi_stdout_write({ptr, i32} %str)
            # Accepts fat pointer struct {i8*, i32}
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            arg_value = codegen.expressions.emit_expr(args[0])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [string_struct_ty])
            return codegen.builder.call(stdlib_func, [arg_value], name=f"{stream_name}_write_result")

        elif method == "write_bytes":
            # i32 @sushi_stdout_write_bytes({i32, i32, ptr} %array)
            # Accepts array struct by value
            array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
            arg_value = codegen.expressions.emit_expr(args[0])

            # arg_value is already the struct by value (from emit_name loading the variable)
            # No additional load needed!

            stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [array_struct_ty])
            return codegen.builder.call(stdlib_func, [arg_value], name=f"{stream_name}_write_bytes_result")

    raise_internal_error("CE0028", method=method)


def emit_stdlib_file_call(
    codegen: 'LLVMCodegen',
    file_ptr: ir.Value,
    method: str,
    args: list,
    to_i1: bool
) -> ir.Value:
    """Emit a call to a stdlib file method.

    This function emits an external call to a precompiled stdlib function
    for file operations.

    Args:
        codegen: The LLVM code generator
        file_ptr: The FILE* pointer value
        method: The method name (e.g., "read", "write", "seek")
        args: The method arguments
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the method is not implemented in stdlib
    """
    require_builder(codegen)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    # Build function name: sushi_file_{method}
    func_name = f"sushi_file_{method}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # Handle each method type
    if method in ("read", "readln", "readch"):
        # {ptr, i32} @sushi_file_{method}(ptr %file_ptr)
        # Returns fat pointer struct {i8*, i32}
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name=f"file_{method}_result")
        return result

    elif method == "lines":
        # {i32, i32, {i8*, i32}*} @sushi_file_lines(ptr %file_ptr)
        # Returns iterator struct with fat pointer element type
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
        iterator_struct_ty = ir.LiteralStructType([i32, i32, string_struct_ty.as_pointer()])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, iterator_struct_ty, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_lines_result")
        return result

    elif method in ("write", "writeln"):
        # i32 @sushi_file_{method}(ptr %file_ptr, {ptr, i32} %string)
        # Accepts fat pointer struct {i8*, i32}
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
        arg_value = codegen.expressions.emit_expr(args[0])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, string_struct_ty])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name=f"file_{method}_result")
        return result

    elif method == "read_bytes":
        # {i32, i32, i8*} @sushi_file_read_bytes(ptr %file_ptr, i32 %count)
        # Returns array struct by value
        array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
        arg_value = codegen.expressions.emit_expr(args[0])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, array_struct_ty, [i8_ptr, i32])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name="file_read_bytes_result")

        # Store result in a slot and return pointer (to match variable storage conventions)
        result_slot = codegen.builder.alloca(array_struct_ty, name="read_bytes_slot")
        codegen.builder.store(result, result_slot)
        return result_slot

    elif method == "write_bytes":
        # i32 @sushi_file_write_bytes(ptr %file_ptr, {i32, i32, i8*} %array_struct)
        # Accepts array struct by value
        array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
        arg_value = codegen.expressions.emit_expr(args[0])

        # arg_value is already the struct by value (from emit_name loading the variable)
        # No additional load needed!

        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, array_struct_ty])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name="file_write_bytes_result")
        return result

    elif method == "seek":
        # i32 @sushi_file_seek(ptr %file_ptr, i64 %offset, ptr %seekfrom)
        offset_value = codegen.expressions.emit_expr(args[0])
        seekfrom_value = codegen.expressions.emit_expr(args[1])

        # SeekFrom is a unit enum (no associated data)
        # Use correct type: {i32 tag, [1 x i8] data}
        seekfrom_struct_ty = ir.LiteralStructType([i32, ir.ArrayType(i8, 1)])

        # seekfrom_value is the enum by value (from emit_name loading it)
        # Stdlib expects a pointer, so store it in a slot
        seekfrom_slot = codegen.builder.alloca(seekfrom_struct_ty, name="seekfrom_slot")
        codegen.builder.store(seekfrom_value, seekfrom_slot)

        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, i64, seekfrom_struct_ty.as_pointer()])
        result = codegen.builder.call(stdlib_func, [file_ptr, offset_value, seekfrom_slot], name="file_seek_result")
        return result

    elif method == "tell":
        # i64 @sushi_file_tell(ptr %file_ptr)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i64, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_tell_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif method == "close":
        # i32 @sushi_file_close(ptr %file_ptr)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_close_result")
        return result

    elif method == "is_open":
        # i32 @sushi_file_is_open(ptr %file_ptr)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_is_open_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    raise_internal_error("CE0028", method=method)


def emit_files_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to an io/files module function.

    This function emits an external call to a precompiled stdlib file utility function.
    Maps user-facing function names to their internal sushi_io_files_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the function is not a recognized files function
    """
    require_builder(codegen)

    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_io_files_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # String type: {i8* data, i32 size}
    string_type = codegen.types.ll_type(BuiltinType.STRING)

    if func_name in ["exists", "is_file", "is_dir"]:
        # These functions return i8 (bool) and take one string argument
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        path_value = codegen.expressions.emit_expr(expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i8, [string_type])
        result = codegen.builder.call(stdlib_func, [path_value], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name in ["file_size", "remove", "rmdir"]:
        # These take one string argument and return Result<i32> or Result<i64>
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        path_value = codegen.expressions.emit_expr(expr.args[0])

        if func_name == "file_size":
            # Result<i64> is {i32 tag, [8 x i8] data}
            data_array_type = ir.ArrayType(i8, 8)
        else:
            # Result<i32> is {i32 tag, [4 x i8] data}
            data_array_type = ir.ArrayType(i8, 4)

        result_type = ir.LiteralStructType([i32, data_array_type])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [string_type])
        result = codegen.builder.call(stdlib_func, [path_value], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "rename" or func_name == "copy":
        # rename(string old_path, string new_path) -> Result<i32>
        # copy(string src, string dst) -> Result<i32>
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        arg1_value = codegen.expressions.emit_expr(expr.args[0])
        arg2_value = codegen.expressions.emit_expr(expr.args[1])

        data_array_type = ir.ArrayType(i8, 4)
        result_type = ir.LiteralStructType([i32, data_array_type])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [string_type, string_type])
        result = codegen.builder.call(stdlib_func, [arg1_value, arg2_value], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "mkdir":
        # mkdir(string path, i32 mode) -> Result<i32>
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        path_value = codegen.expressions.emit_expr(expr.args[0])
        mode_value = codegen.expressions.emit_expr(expr.args[1])

        data_array_type = ir.ArrayType(i8, 4)
        result_type = ir.LiteralStructType([i32, data_array_type])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [string_type, i32])
        result = codegen.builder.call(stdlib_func, [path_value, mode_value], name="mkdir_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    else:
        raise_internal_error("CE0024", type="io/files", method=func_name)
