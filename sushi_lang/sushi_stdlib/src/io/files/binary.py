"""File binary I/O methods IR generation."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_fread, declare_fwrite, declare_malloc
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error


def generate_read_bytes(module: ir.Module) -> None:
    """Generate IR for file.read_bytes(i32) -> u8[]"""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()

    array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])

    fn_ty = ir.FunctionType(array_struct_ty, [i8_ptr, i32])
    fn = ir.Function(module, fn_ty, name="sushi_file_read_bytes")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    count_val = fn.args[1]
    count_val.name = "count"

    fread_fn = declare_fread(module)
    malloc_fn = declare_malloc(module)

    array_slot = builder.alloca(array_struct_ty, name="read_bytes_array")

    count_i64 = builder.zext(count_val, i64, name="count_i64")
    buffer = builder.call(malloc_fn, [count_i64])

    null_ptr = ir.Constant(i8_ptr, None)
    is_null = builder.icmp_unsigned('==', buffer, null_ptr)

    fail_block = builder.append_basic_block("alloc_fail")
    continue_block = builder.append_basic_block("alloc_ok")
    builder.cbranch(is_null, fail_block, continue_block)

    builder.position_at_end(fail_block)
    emit_runtime_error(module, builder, "RE2021")

    builder.position_at_end(continue_block)

    one = ir.Constant(i64, 1)
    bytes_read = builder.call(fread_fn, [buffer, one, count_i64, file_ptr])

    bytes_read_i32 = builder.trunc(bytes_read, i32)

    zero = ir.Constant(i32, 0)

    len_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 0)])
    builder.store(bytes_read_i32, len_ptr)

    cap_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 1)])
    builder.store(count_val, cap_ptr)

    data_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 2)])
    builder.store(buffer, data_ptr)

    array_struct = builder.load(array_slot, name="array_struct")
    builder.ret(array_struct)


def generate_write_bytes(module: ir.Module) -> None:
    """Generate IR for file.write_bytes(u8[]) -> ~"""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()

    array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])

    fn_ty = ir.FunctionType(i32, [i8_ptr, array_struct_ty])
    fn = ir.Function(module, fn_ty, name="sushi_file_write_bytes")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    array_struct = fn.args[1]
    array_struct.name = "array_struct"

    array_slot = builder.alloca(array_struct_ty, name="array_slot")
    builder.store(array_struct, array_slot)

    fwrite_fn = declare_fwrite(module)

    zero = ir.Constant(i32, 0)

    len_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 0)])
    length = builder.load(len_ptr)

    data_ptr_ptr = builder.gep(array_slot, [zero, ir.Constant(i32, 2)])
    data_ptr = builder.load(data_ptr_ptr)

    one = ir.Constant(i64, 1)
    length_i64 = builder.zext(length, i64)
    builder.call(fwrite_fn, [data_ptr, one, length_i64, file_ptr])

    builder.ret(ir.Constant(i32, 0))
