"""
stdout module - Standard output stream methods.

This module implements IR generation for stdout methods:
- write(string) -> ~: Write string to stdout without newline
- write_bytes(u8[]) -> ~: Write byte array to stdout
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_fwrite
from sushi_lang.sushi_stdlib.src.io.stdio.common import declare_stdout_handle


def generate_stdout_write(module: ir.Module) -> None:
    """Generate IR for stdout.write(string) -> ~.

    Writes a string to stdout without adding a newline.
    Uses fwrite with fat pointer length instead of fputs for proper handling.

    Args:
        module: The LLVM module to add the function to.
    """
    # Declare external functions
    fwrite_fn = declare_fwrite(module)
    stdout_handle = declare_stdout_handle(module)

    # Function signature: i32 @sushi_stdout_write({ptr, i32} %str)
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
    fn_ty = ir.FunctionType(i32, [string_struct_ty])
    func = ir.Function(module, fn_ty, name="sushi_stdout_write")
    func.args[0].name = "str"

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    string_val = func.args[0]

    # Extract data pointer and length from fat pointer struct
    string_data = builder.extract_value(string_val, 0, name="string_data")
    string_len = builder.extract_value(string_val, 1, name="string_len")

    # Call fwrite(string_data, 1, string_len, stdout)
    # fwrite doesn't require null termination - writes exactly string_len bytes
    string_len_i64 = builder.zext(string_len, i64, name="string_len_i64")
    one_i64 = ir.Constant(i64, 1)
    stdout_ptr = builder.load(stdout_handle, name="stdout")
    builder.call(fwrite_fn, [string_data, one_i64, string_len_i64, stdout_ptr])

    # Return blank value (i32 0)
    zero = ir.Constant(i32, 0)
    builder.ret(zero)


def generate_stdout_write_bytes(module: ir.Module) -> None:
    """Generate IR for stdout.write_bytes(u8[]) -> ~.

    Writes a byte array to stdout.

    Args:
        module: The LLVM module to add the function to.
    """
    # Declare external functions
    fwrite_fn = declare_fwrite(module)
    stdout_handle = declare_stdout_handle(module)

    # Function signature: i32 @sushi_stdout_write_bytes({i32, i32, ptr} %array)
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = i8.as_pointer()

    # Dynamic array struct: {i32 len, i32 cap, u8* data}
    array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])

    fn_ty = ir.FunctionType(i32, [array_struct_ty])
    func = ir.Function(module, fn_ty, name="sushi_stdout_write_bytes")
    func.args[0].name = "array"

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    array_val = func.args[0]

    # Extract length and data pointer from array struct
    length = builder.extract_value(array_val, 0, name="length")
    data_ptr = builder.extract_value(array_val, 2, name="data_ptr")

    # Call fwrite(data, 1, length, stdout)
    length_i64 = builder.zext(length, i64, name="length_i64")
    one_i64 = ir.Constant(i64, 1)
    stdout_ptr = builder.load(stdout_handle, name="stdout")
    builder.call(fwrite_fn, [data_ptr, one_i64, length_i64, stdout_ptr])

    # Return blank value (i32 0)
    zero = ir.Constant(i32, 0)
    builder.ret(zero)
