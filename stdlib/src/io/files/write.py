"""
File writing methods IR generation.

Implements IR generation for:
- write(string) - Write string without newline
- writeln(string) - Write string with newline
"""

import llvmlite.ir as ir
from stdlib.src.libc_declarations import declare_fwrite
from stdlib.src.string_helpers import create_string_constant


def generate_write(module: ir.Module) -> None:
    """Generate IR for file.write(string) -> ~

    Writes string to file without newline.
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: i32 @sushi_file_write(ptr %file_ptr, {ptr, i32} %string)
    fn_ty = ir.FunctionType(i32, [i8_ptr, string_struct_type])
    fn = ir.Function(module, fn_ty, name="sushi_file_write")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get parameters
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    string_val = fn.args[1]
    string_val.name = "string"

    # Extract data pointer and length from fat pointer struct
    string_data = builder.extract_value(string_val, 0, name="string_data")
    string_len = builder.extract_value(string_val, 1, name="string_len")

    # Declare fwrite
    fwrite_fn = declare_fwrite(module)

    # Call fwrite(string_data, 1, string_len, file_ptr)
    string_len_i64 = builder.zext(string_len, i64, name="string_len_i64")
    one_i64 = ir.Constant(i64, 1)
    builder.call(fwrite_fn, [string_data, one_i64, string_len_i64, file_ptr])

    # Return blank value (i32 0)
    builder.ret(ir.Constant(i32, 0))


def generate_writeln(module: ir.Module) -> None:
    """Generate IR for file.writeln(string) -> ~

    Writes string to file with newline.
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: i32 @sushi_file_writeln(ptr %file_ptr, {ptr, i32} %string)
    fn_ty = ir.FunctionType(i32, [i8_ptr, string_struct_type])
    fn = ir.Function(module, fn_ty, name="sushi_file_writeln")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get parameters
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    string_val = fn.args[1]
    string_val.name = "string"

    # Extract data pointer and length from fat pointer struct
    string_data = builder.extract_value(string_val, 0, name="string_data")
    string_len = builder.extract_value(string_val, 1, name="string_len")

    # Declare fwrite
    fwrite_fn = declare_fwrite(module)

    # Call fwrite(string_data, 1, string_len, file_ptr)
    string_len_i64 = builder.zext(string_len, i64, name="string_len_i64")
    one_i64 = ir.Constant(i64, 1)
    builder.call(fwrite_fn, [string_data, one_i64, string_len_i64, file_ptr])

    # Call fwrite("\n", 1, 1, file_ptr)
    newline_str = create_string_constant(module, builder, "\n", name="str_newline")
    builder.call(fwrite_fn, [newline_str, one_i64, one_i64, file_ptr])

    # Return blank value (i32 0)
    builder.ret(ir.Constant(i32, 0))
