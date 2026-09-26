"""errno to a FileError tag for the <io/files> generators, over `src/errno_tags.py`."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_errno_location
from sushi_lang.backend.runtime.constants import (
    ERRNO_EACCES,
    ERRNO_EEXIST,
    ERRNO_EINTR,
    ERRNO_EIO,
    ERRNO_EISDIR,
    ERRNO_EMFILE,
    ERRNO_ENOENT,
    ERRNO_ENOSPC,
    ERRNO_ENOTDIR,
    ERRNO_EPERM,
)
from sushi_lang.sushi_stdlib.src import errno_tags
from sushi_lang.sushi_stdlib.src._platform import get_platform_module

ERRNO_DEFAULT_FILE_ERROR = 8  # Other


def errno_to_file_error_table() -> dict[int, int]:
    """errno value -> FileError variant tag, for the build platform.

    The tag order is CollectorPass._register_predefined_enums. ENAMETOOLONG and
    ELOOP differ between the platforms, so the platform module is their one home.
    An unmapped errno maps to ERRNO_DEFAULT_FILE_ERROR.
    """
    files = get_platform_module('files')
    return {
        ERRNO_ENOENT: 0,        # NotFound
        ERRNO_EPERM: 1,         # PermissionDenied
        ERRNO_EACCES: 1,        # PermissionDenied
        ERRNO_EEXIST: 2,        # AlreadyExists
        ERRNO_EISDIR: 3,        # IsDirectory
        ERRNO_ENOSPC: 4,        # DiskFull
        ERRNO_EMFILE: 5,        # TooManyOpen
        files.ENAMETOOLONG: 6,  # InvalidPath
        ERRNO_ENOTDIR: 6,       # InvalidPath
        files.ELOOP: 6,         # InvalidPath
        ERRNO_EIO: 7,           # IOError
    }


def emit_file_error_tag(builder: ir.IRBuilder, module: ir.Module) -> ir.Value:
    """Read errno and map it to a FileError variant tag (i32)."""
    return errno_tags.emit_errno_tag(
        builder, module, errno_to_file_error_table(),
        ERRNO_DEFAULT_FILE_ERROR)


def emit_errno_err_result(builder: ir.IRBuilder, module: ir.Module,
                          result_type: ir.LiteralStructType) -> ir.Value:
    """The whole failure path: read errno, map it, build Result.Err(FileError)."""
    return errno_tags.emit_errno_err_result(
        builder, module, result_type,
        errno_to_file_error_table(),
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
