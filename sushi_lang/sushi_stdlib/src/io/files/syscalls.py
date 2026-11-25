"""The one-syscall wrappers of <io/files>: remove, rmdir, mkdir, rename.

Each exported body is one zero-on-success libc call. The frame around the
call -- Result.Ok(0), or errno mapped into the FileError -- is written once,
in _generate_int_syscall; a wrapper contributes its symbol, its libc
declaration and its parameter list, and nothing else.
"""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_result_type, get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import emit_errno_err_result
from sushi_lang.sushi_stdlib.src.results import emit_ok_result


def generate_ir(module: ir.Module) -> None:
    """Generate LLVM IR for the syscall wrappers."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    _generate_int_syscall(module, "sushi_io_files_remove", "declare_unlink", [i8_ptr])
    _generate_int_syscall(module, "sushi_io_files_rmdir", "declare_rmdir", [i8_ptr])
    _generate_int_syscall(module, "sushi_io_files_mkdir", "declare_mkdir", [i8_ptr, i32])
    _generate_int_syscall(module, "sushi_io_files_rename", "declare_rename",
                          [i8_ptr, i8_ptr])


def _generate_int_syscall(module: ir.Module, sushi_name: str, declare_name: str,
                          param_types: list) -> None:
    """Emit `Result<i32> <sushi_name>(...)`: one zero-on-success libc call.

    Every path argument arrives already marshalled as a C string, and the
    CALLER frees it (#292).
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    libc_func = getattr(platform_files, declare_name)(module)

    # Result<i32, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2):
    # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(4, 16)/8 = 2
    result_type = get_result_type(i32, get_unit_enum_type())

    func = ir.Function(module, ir.FunctionType(result_type, param_types), name=sushi_name)
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    result = builder.call(libc_func, list(func.args), name="syscall_result")
    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, zero, 4))

