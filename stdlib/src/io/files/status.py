"""
File status methods IR generation.

Implements IR generation for:
- close() - Close file and release resources
- is_open() - Check if file is open
"""

import llvmlite.ir as ir
from stdlib.src.libc_declarations import declare_fclose


def generate_close(module: ir.Module) -> None:
    """Generate IR for file.close() -> ~

    Closes file if not NULL.
    """
    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()

    # Function signature: i32 @sushi_file_close(ptr %file_ptr)
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_close")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get file pointer parameter
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    # Declare fclose
    fclose_fn = declare_fclose(module)

    # Check if file pointer is not NULL before closing
    null_ptr = ir.Constant(i8_ptr, None)
    is_null = builder.icmp_unsigned('==', file_ptr, null_ptr)

    close_block = builder.append_basic_block("file_close")
    merge_block = builder.append_basic_block("file_close_done")

    builder.cbranch(is_null, merge_block, close_block)

    # Close the file if not NULL
    builder.position_at_end(close_block)
    builder.call(fclose_fn, [file_ptr])
    builder.branch(merge_block)

    # Merge block
    builder.position_at_end(merge_block)

    # Return blank value (i32 0)
    builder.ret(ir.Constant(i32, 0))


def generate_is_open(module: ir.Module) -> None:
    """Generate IR for file.is_open() -> bool

    Checks if file pointer is not NULL.
    """
    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()

    # Function signature: i32 @sushi_file_is_open(ptr %file_ptr)
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_is_open")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get file pointer parameter
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    # Check if file pointer is not NULL
    null_ptr = ir.Constant(i8_ptr, None)
    is_not_null = builder.icmp_unsigned('!=', file_ptr, null_ptr)

    # Convert to i32 bool (0 or 1)
    result = builder.zext(is_not_null, i32)

    # Return result
    builder.ret(result)
