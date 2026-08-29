"""errno access and FileError mapping for the <io/files> generators.

The one errno seam for `.bc` code: read errno after a failed libc call, map it
to a FileError variant tag, and build the Result.Err value. The mapping table
is shared with the compiler-inline open() path (backend/runtime/constants.py).
"""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.backend.runtime.constants import (
    errno_to_file_error_table,
    ERRNO_DEFAULT_FILE_ERROR,
)


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


def emit_err_result(builder: ir.IRBuilder, result_type: ir.LiteralStructType,
                    error_tag: ir.Value) -> ir.Value:
    """Build Result.Err(FileError) with the given variant tag in the payload.

    A FileError value is {i32 tag, [1 x i64] zeros}; inside the Result data
    array its tag sits in the first four bytes.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    data_array_type = result_type.elements[1]

    data_alloca = builder.alloca(data_array_type, name="err_data")
    builder.store(ir.Constant(data_array_type, None), data_alloca)
    tag_ptr = builder.bitcast(data_alloca, i32.as_pointer(), name="err_tag_ptr")
    builder.store(error_tag, tag_ptr)
    data_value = builder.load(data_alloca, name="err_data_value")

    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, ir.Constant(i32, 1), 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, data_value, 1, name="err_result")
    return err_result


def emit_errno_err_result(builder: ir.IRBuilder, module: ir.Module,
                          result_type: ir.LiteralStructType) -> ir.Value:
    """The whole failure path: read errno, map it, build Result.Err(FileError)."""
    return emit_err_result(builder, result_type, emit_file_error_tag(builder, module))
