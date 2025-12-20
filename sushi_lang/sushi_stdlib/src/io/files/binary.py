"""
File binary I/O methods IR generation.

Implements IR generation for:
- read_bytes(i32) - Read N bytes into u8[]
- write_bytes(u8[]) - Write byte array to file
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_fread, declare_fwrite, declare_malloc
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error


def generate_read_bytes(module: ir.Module) -> None:
    """Generate IR for file.read_bytes(i32) -> u8[]

    Reads N bytes from file and returns u8[] byte array.
    Array struct: {i32 len, i32 cap, ptr data}
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()

    # Dynamic array struct type: {i32 len, i32 cap, ptr data}
    array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])

    # Function signature: {i32, i32, i8*} @sushi_file_read_bytes(ptr %file_ptr, i32 %count)
    # Returns array struct by value (consistent with Sushi's internal array handling)
    fn_ty = ir.FunctionType(array_struct_ty, [i8_ptr, i32])
    fn = ir.Function(module, fn_ty, name="sushi_file_read_bytes")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get parameters
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    count_val = fn.args[1]
    count_val.name = "count"

    # Declare required functions
    fread_fn = declare_fread(module)
    malloc_fn = declare_malloc(module)

    # Allocate array struct on stack
    array_slot = builder.alloca(array_struct_ty, name="read_bytes_array")

    # Allocate buffer for reading bytes (extend i32 count to i64 for malloc)
    count_i64 = builder.zext(count_val, i64, name="count_i64")
    buffer = builder.call(malloc_fn, [count_i64])

    # Check for allocation failure
    null_ptr = ir.Constant(i8_ptr, None)
    is_null = builder.icmp_unsigned('==', buffer, null_ptr)

    fail_block = builder.append_basic_block("alloc_fail")
    continue_block = builder.append_basic_block("alloc_ok")
    builder.cbranch(is_null, fail_block, continue_block)

    # Allocation failed
    builder.position_at_end(fail_block)
    emit_runtime_error(module, builder, "RE2021", "memory allocation failed")

    # Allocation succeeded - read bytes
    builder.position_at_end(continue_block)

    # Call fread(buffer, 1, count, file_ptr) - count_i64 already created above
    one = ir.Constant(i64, 1)
    bytes_read = builder.call(fread_fn, [buffer, one, count_i64, file_ptr])

    # Truncate bytes_read from i64 to i32
    bytes_read_i32 = builder.trunc(bytes_read, i32)

    # Initialize array struct fields
    zero = ir.Constant(i32, 0)

    # Set length = bytes actually read
    len_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 0)])
    builder.store(bytes_read_i32, len_ptr)

    # Set capacity = requested count
    cap_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 1)])
    builder.store(count_val, cap_ptr)

    # Set data pointer
    data_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 2)])
    builder.store(buffer, data_ptr)

    # Load and return array struct by value
    array_struct = builder.load(array_slot, name="array_struct")
    builder.ret(array_struct)


def generate_write_bytes(module: ir.Module) -> None:
    """Generate IR for file.write_bytes(u8[]) -> ~

    Writes byte array to file.
    Array struct: {i32 len, i32 cap, ptr data}
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()

    # Dynamic array struct type: {i32 len, i32 cap, ptr data}
    array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])

    # Function signature: i32 @sushi_file_write_bytes(ptr %file_ptr, {i32, i32, i8*} %array_struct)
    # Accepts array struct by value (consistent with Sushi's internal array handling)
    fn_ty = ir.FunctionType(i32, [i8_ptr, array_struct_ty])
    fn = ir.Function(module, fn_ty, name="sushi_file_write_bytes")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get parameters
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    array_struct = fn.args[1]
    array_struct.name = "array_struct"

    # Allocate slot for struct and store it (so we can GEP into it)
    array_slot = builder.alloca(array_struct_ty, name="array_slot")
    builder.store(array_struct, array_slot)

    # Declare fwrite
    fwrite_fn = declare_fwrite(module)

    # Load array struct fields
    zero = ir.Constant(i32, 0)

    # Get length
    len_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 0)])
    length = builder.load(len_ptr)

    # Get data pointer
    data_ptr_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 2)])
    data_ptr = builder.load(data_ptr_ptr)

    # Call fwrite(data, 1, length, file_ptr)
    one = ir.Constant(i64, 1)
    length_i64 = builder.zext(length, i64)
    builder.call(fwrite_fn, [data_ptr, one, length_i64, file_ptr])

    # Return blank value (i32 0)
    builder.ret(ir.Constant(i32, 0))
