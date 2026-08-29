"""errno access and FileError mapping for the <io/files> generators.

The one errno seam for `.bc` code: read errno after a failed libc call and map
it to a FileError variant tag. The mapping table is shared with the
compiler-inline open() path (backend/runtime/constants.py); the Result byte
layout itself lives in results.py.
"""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.backend.runtime.constants import (
    errno_to_file_error_table,
    ERRNO_DEFAULT_FILE_ERROR,
)
from sushi_lang.sushi_stdlib.src.io.files.results import emit_err_result


def declare_errno_location(module: ir.Module) -> ir.Function:
    """Declare the errno accessor: int* __error() (macOS) / __errno_location() (Linux)."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    name = "__errno_location" if get_current_platform().is_linux else "__error"
    try:
        return module.get_global(name)
    except KeyError:
        func_type = ir.FunctionType(i32.as_pointer(), [])
        return ir.Function(module, func_type, name=name)


def emit_file_error_tag(builder: ir.IRBuilder, module: ir.Module) -> ir.Value:
    """Read errno and map it to a FileError variant tag (i32).

    Call this directly after the failed libc call; a later libc call (close,
    free) can overwrite errno.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    errno_fn = declare_errno_location(module)
    errno_ptr = builder.call(errno_fn, [], name="errno_ptr")
    errno_value = builder.load(errno_ptr, name="errno_value")

    result = ir.Constant(i32, ERRNO_DEFAULT_FILE_ERROR)
    table = errno_to_file_error_table(get_current_platform().is_linux)
    for errno_val, tag in reversed(list(table.items())):
        is_match = builder.icmp_signed("==", errno_value, ir.Constant(i32, errno_val))
        result = builder.select(is_match, ir.Constant(i32, tag), result)
    return result


def emit_errno_err_result(builder: ir.IRBuilder, module: ir.Module,
                          result_type: ir.LiteralStructType) -> ir.Value:
    """The whole failure path: read errno, map it, build Result.Err(FileError)."""
    return emit_err_result(builder, result_type, emit_file_error_tag(builder, module))
