"""Standard library I/O call emission (stdio, file, files)."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg, emit_cstr_arg

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

    The arguments are emitted ONCE, here, through the built-in call-argument seam. A
    stream method BORROWS what it writes, so an owning temporary handed to `write` had
    no owner at all and leaked (#475).
    """
    require_builder(codegen)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    func_name = f"sushi_{stream_name}_{method}"
    arg_values = [emit_borrowed_arg(codegen, arg) for arg in args]

    from sushi_lang.backend.functions import declare_stdlib_function

    if method == "flush":
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [])
        return codegen.builder.call(stdlib_func, [], name=f"{stream_name}_flush_result")

    # The one method valid on every stream, so it is resolved before the stream split.
    if method == "is_terminal":
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i8, [])
        result = codegen.builder.call(stdlib_func, [], name=f"{stream_name}_is_terminal_result")
        return codegen.utils.as_i1(result) if to_i1 else result

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
            arg_value = arg_values[0]
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
            arg_value = arg_values[0]
            stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [string_struct_ty])
            return codegen.builder.call(stdlib_func, [arg_value], name=f"{stream_name}_write_result")

        elif method == "write_bytes":
            array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
            arg_value = arg_values[0]

            stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [array_struct_ty])
            return codegen.builder.call(stdlib_func, [arg_value], name=f"{stream_name}_write_bytes_result")

    raise_internal_error("CE0028", method=method)


def emit_stdlib_file_call(
    codegen: 'LLVMCodegen',
    fd: ir.Value,
    method: str,
    args: list,
    to_i1: bool
) -> ir.Value:
    """Emit the ONE builtin File method, `lines()`, over a descriptor.

    The other twelve became extension methods in `src_sushi/io/fs.sushi` and reach the
    descriptor primitives like any other Sushi code, so this emitter has one arm where it
    had eleven. Ruling R13 keeps `lines()` here until Phase 7 decides what line iteration
    becomes.
    """
    require_builder(codegen)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    if method != "lines":
        raise_internal_error("CE0024", type="File", method=method)
    if args:
        raise_internal_error("CE0023", method="File.lines", expected=0, got=len(args))

    from sushi_lang.backend.functions import declare_stdlib_function

    string_struct_ty = ir.LiteralStructType([i8_ptr, i32, i8])  # {data, size, owned} (#145)
    iterator_struct_ty = ir.LiteralStructType([i32, i32, string_struct_ty.as_pointer()])
    stdlib_func = declare_stdlib_function(codegen.module, "sushi_file_lines",
                                          iterator_struct_ty, [i32])
    return codegen.builder.call(stdlib_func, [fd], name="file_lines_result")



def emit_files_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to an io/files module function."""
    require_builder(codegen)

    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)

    stdlib_func_name = f"sushi_io_files_{func_name}"

    from sushi_lang.backend.functions import declare_stdlib_function

    # Every path argument is marshalled HERE and freed at scope exit, like an FFI
    # argument (#292). The callees take `i8*` and free nothing.
    i8_ptr = codegen.types.i8.as_pointer()

    if func_name in ["exists", "is_file", "is_dir"]:
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        path_cstr = emit_cstr_arg(codegen, expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i8, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [path_cstr], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name in ["file_size", "remove", "rmdir", "mtime", "ctime", "mode",
                       "is_symlink"]:
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        path_cstr = emit_cstr_arg(codegen, expr.args[0])

        # Result<i64|i32|i8, FileError> is {i32 tag, [2 x i64] data} (#300 phase 2):
        # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(payload, 16)/8 = 2.
        # Shared helper keeps this byte-matched with the stdlib .bc.
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        i64 = ir.IntType(64)
        if func_name in ("file_size", "mtime", "ctime"):
            ok_type = i64
        elif func_name == "is_symlink":
            ok_type = i8
        else:
            ok_type = i32
        result_type = get_result_type(ok_type, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [path_cstr], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "read_dir":
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))
        path_cstr = emit_cstr_arg(codegen, expr.args[0])

        # Result<string[], FileError>: the descriptor is 16 bytes, so the data
        # array is [2 x i64] again. Shared helper keeps it byte-matched.
        from sushi_lang.sushi_stdlib.src.type_definitions import (
            get_result_type, get_unit_enum_type, get_dynamic_array_type, get_string_type,
        )
        array_type = get_dynamic_array_type(get_string_type())
        result_type = get_result_type(array_type, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [i8_ptr])
        return codegen.builder.call(stdlib_func, [path_cstr], name="read_dir_result")

    elif func_name == "rename" or func_name == "copy":
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        arg1_cstr = emit_cstr_arg(codegen, expr.args[0])
        arg2_cstr = emit_cstr_arg(codegen, expr.args[1])

        # Result<i32, FileError> is {i32 tag, [2 x i64] data} (#300 phase 2, see file_size)
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        result_type = get_result_type(i32, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [i8_ptr, i8_ptr])
        result = codegen.builder.call(stdlib_func, [arg1_cstr, arg2_cstr], name=f"{func_name}_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "mkdir":
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        path_cstr = emit_cstr_arg(codegen, expr.args[0])
        mode_value = emit_borrowed_arg(codegen, expr.args[1])

        # Result<i32, FileError> is {i32 tag, [2 x i64] data} (#300 phase 2, see file_size)
        from sushi_lang.sushi_stdlib.src.type_definitions import get_result_type, get_unit_enum_type
        result_type = get_result_type(i32, get_unit_enum_type())
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, result_type, [i8_ptr, i32])
        result = codegen.builder.call(stdlib_func, [path_cstr, mode_value], name="mkdir_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name in _DESCRIPTOR_SIGNATURES:
        result = _emit_descriptor_call(codegen, expr, func_name, stdlib_func_name)
        return codegen.utils.as_i1(result) if to_i1 else result

    else:
        raise_internal_error("CE0024", type="io/files", method=func_name)


def _descriptor_signatures():
    """One table: what each descriptor primitive takes, and what its Ok payload is.

    A signature is written ONCE and read for the parameter types, the arity and the
    result type together. The five Phase 4 entries used to be an arity dict beside three
    if-arms that repeated the same types, and adding the six sequential ones would have
    tripled that.

    A `None` ok type means the function answers its value BARE rather than in a Result --
    `fd_isatty` is the only one, because asking cannot fail.
    """
    from sushi_lang.sushi_stdlib.src.type_definitions import (
        get_byte_array_type, get_maybe_type, get_string_type,
    )
    i8_ptr = ir.PointerType(ir.IntType(8))
    i32, i64 = ir.IntType(32), ir.IntType(64)
    bytes_ty, string_ty = get_byte_array_type(), get_string_type()
    maybe_string_ty = get_maybe_type(string_ty)

    # name -> (parameter types, Ok payload type or None, index of a path argument)
    return {
        "fd_open":      ([i8_ptr, i32, i32], i32, 0),
        "fd_pread":     ([i32, i64, i32], bytes_ty, None),
        "fd_pwrite":    ([i32, i64, bytes_ty], i32, None),
        "fd_dup":       ([i32], i32, None),
        "fd_close":     ([i32], i32, None),
        "fd_read":      ([i32, i32], bytes_ty, None),
        "fd_write":     ([i32, bytes_ty], i32, None),
        "fd_write_str": ([i32, string_ty], i32, None),
        "fd_readln":    ([i32], maybe_string_ty, None),
        "fd_seek":      ([i32, i64, i32], i64, None),
        "fd_isatty":    ([i32], None, None),
    }


_DESCRIPTOR_SIGNATURES = _descriptor_signatures()


def _emit_descriptor_call(codegen, expr, func_name: str, stdlib_func_name: str):
    """The descriptor layer of <io/files> (HANDLES.md, Phases 4 and 5).

    A path is marshalled through `emit_cstr_arg`, the one C-string seam; everything else
    crosses as the value it already is -- a descriptor is a bare i32, an offset a bare
    i64, a `u8[]` its descriptor by value and a `string` its fat pointer. None of those
    needs marshalling, and each is a BORROW: no callee here frees what it was handed.
    """
    from sushi_lang.backend.functions import declare_stdlib_function
    from sushi_lang.sushi_stdlib.src.type_definitions import (
        get_result_type, get_unit_enum_type,
    )

    param_types, ok_type, path_index = _DESCRIPTOR_SIGNATURES[func_name]
    if len(expr.args) != len(param_types):
        raise_internal_error("CE0023", method=func_name,
                             expected=len(param_types), got=len(expr.args))

    args = [emit_cstr_arg(codegen, a) if i == path_index else emit_borrowed_arg(codegen, a)
            for i, a in enumerate(expr.args)]

    return_type = (ir.IntType(8) if ok_type is None
                   else get_result_type(ok_type, get_unit_enum_type()))
    stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name,
                                          return_type, param_types)
    return codegen.builder.call(stdlib_func, args, name=f"{func_name}_result")
