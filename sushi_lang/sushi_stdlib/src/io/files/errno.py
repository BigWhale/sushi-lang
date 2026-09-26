"""errno to a FileError tag for the <io/files> generators, over `src/errno_tags.py`."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_errno_location
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.backend.runtime.constants import (
    errno_to_file_error_table,
    ERRNO_DEFAULT_FILE_ERROR,
    ERRNO_EINTR,
)
from sushi_lang.sushi_stdlib.src import errno_tags


def emit_file_error_tag(builder: ir.IRBuilder, module: ir.Module) -> ir.Value:
    """Read errno and map it to a FileError variant tag (i32)."""
    return errno_tags.emit_errno_tag(
        builder, module, errno_to_file_error_table(get_current_platform().is_linux),
        ERRNO_DEFAULT_FILE_ERROR)


def emit_errno_err_result(builder: ir.IRBuilder, module: ir.Module,
                          result_type: ir.LiteralStructType) -> ir.Value:
    """The whole failure path: read errno, map it, build Result.Err(FileError)."""
    return errno_tags.emit_errno_err_result(
        builder, module, result_type,
        errno_to_file_error_table(get_current_platform().is_linux),
        ERRNO_DEFAULT_FILE_ERROR)


def emit_is_eintr(builder: ir.IRBuilder, module: ir.Module) -> ir.Value:
    """Was the call that just failed interrupted by a signal? Answers an i1.

    A read or a write that a signal interrupts before any byte moves answers -1 with
    EINTR, and the caller is expected to ask again. Nothing in this layer did, so a
    program that takes a signal saw a read failure that was not one.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    errno_ptr = builder.call(declare_errno_location(module), [], name="errno_ptr")
    return builder.icmp_signed("==", builder.load(errno_ptr, name="errno_value"),
                               ir.Constant(i32, ERRNO_EINTR), name="was_interrupted")
