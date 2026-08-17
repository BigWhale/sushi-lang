"""Standard library I/O call emission (stdio, file, files)."""
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
    """Emit a call to a stdlib stdio method."""
    require_builder(codegen)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    func_name = f"sushi_{stream_name}_{method}"

    from sushi_lang.backend.functions import declare_stdlib_function

    if stream_name == "stdin":
        if method == "readln":
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_readln_result")

        elif method == "read":
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_read_result")

        elif method == "read_bytes":
            array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
            arg_value = codegen.expressions.emit_expr(args[0])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, array_struct_ty, [i32])
            result = codegen.builder.call(stdlib_func, [arg_value], name="stdin_read_bytes_result")

            result_slot = codegen.builder.alloca(array_struct_ty, name="stdin_read_bytes_slot")
            codegen.builder.store(result, result_slot)
            return result_slot

        elif method == "lines":
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            iterator_struct_ty = ir.LiteralStructType([i32, i32, string_struct_ty.as_pointer()])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, iterator_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_lines_result")

    elif stream_name in ["stdout", "stderr"]:
        if method == "write":
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
            arg_value = codegen.expressions.emit_expr(args[0])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [string_struct_ty])
            return codegen.builder.call(stdlib_func, [arg_value], name=f"{stream_name}_write_result")

        elif method == "write_bytes":
            array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
            arg_value = codegen.expressions.emit_expr(args[0])

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
    """Emit a call to a stdlib file method."""
    require_builder(codegen)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    func_name = f"sushi_file_{method}"

    from sushi_lang.backend.functions import declare_stdlib_function

    if method in ("read", "readln", "readch"):
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name=f"file_{method}_result")
        return result

    elif method == "lines":
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
        iterator_struct_ty = ir.LiteralStructType([i32, i32, string_struct_ty.as_pointer()])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, iterator_struct_ty, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_lines_result")
        return result

    elif method in ("write", "writeln"):
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
        arg_value = codegen.expressions.emit_expr(args[0])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, string_struct_ty])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name=f"file_{method}_result")
        return result

    elif method == "read_bytes":
        array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
        arg_value = codegen.expressions.emit_expr(args[0])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, array_struct_ty, [i8_ptr, i32])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name="file_read_bytes_result")

        result_slot = codegen.builder.alloca(array_struct_ty, name="read_bytes_slot")
        codegen.builder.store(result, result_slot)
        return result_slot

    elif method == "write_bytes":
        array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
        arg_value = codegen.expressions.emit_expr(args[0])

        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, array_struct_ty])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name="file_write_bytes_result")
        return result

    elif method == "seek":
        offset_value = codegen.expressions.emit_expr(args[0])
        seekfrom_value = codegen.expressions.emit_expr(args[1])

        # SeekFrom is a unit enum (no associated data)
        # New shape (#300 phase 2): {i32 tag, [1 x i64] data} -- must byte-match the .bc
        from sushi_lang.sushi_stdlib.src.type_definitions import get_unit_enum_type
        seekfrom_struct_ty = get_unit_enum_type()

        seekfrom_slot = codegen.builder.alloca(seekfrom_struct_ty, name="seekfrom_slot")
        codegen.builder.store(seekfrom_value, seekfrom_slot)

        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, i64, seekfrom_struct_ty.as_pointer()])
        result = codegen.builder.call(stdlib_func, [file_ptr, offset_value, seekfrom_slot], name="file_seek_result")
        return result

    elif method == "tell":
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i64, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_tell_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif method == "close":
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_close_result")
        return result

    elif method == "is_open":
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_is_open_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    raise_internal_error("CE0028", method=method)


def emit_files_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to an io/files module function."""
    require_builder(codegen)

    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)

    stdlib_func_name = f"sushi_io_files_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    string_type = codegen.types.ll_type(BuiltinType.STRING)

    if func_name in ["exists", "is_file", "is_dir"]:
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        path_value = codegen.expressions.emit_expr(expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i8, [string_type])
        result = codegen.builder.call(stdlib_func, [path_value], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name in ["file_size", "remove", "rmdir"]:
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        path_value = codegen.expressions.emit_expr(expr.args[0])

        # Result<i64|i32, FileError> is {i32 tag, [2 x i64] data} (#300 phase 2):
        # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(payload, 16)/8 = 2.
        # Shared helper keeps this byte-matched with the stdlib .bc.
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        i64 = ir.IntType(64)
        ok_type = i64 if func_name == "file_size" else i32
        result_type = get_result_type(ok_type, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [string_type])
        result = codegen.builder.call(stdlib_func, [path_value], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "rename" or func_name == "copy":
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        arg1_value = codegen.expressions.emit_expr(expr.args[0])
        arg2_value = codegen.expressions.emit_expr(expr.args[1])

        # Result<i32, FileError> is {i32 tag, [2 x i64] data} (#300 phase 2, see file_size)
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        result_type = get_result_type(i32, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [string_type, string_type])
        result = codegen.builder.call(stdlib_func, [arg1_value, arg2_value], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "mkdir":
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        path_value = codegen.expressions.emit_expr(expr.args[0])
        mode_value = codegen.expressions.emit_expr(expr.args[1])

        # Result<i32, FileError> is {i32 tag, [2 x i64] data} (#300 phase 2, see file_size)
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        result_type = get_result_type(i32, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [string_type, i32])
        result = codegen.builder.call(stdlib_func, [path_value, mode_value], name="mkdir_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    else:
        raise_internal_error("CE0024", type="io/files", method=func_name)
