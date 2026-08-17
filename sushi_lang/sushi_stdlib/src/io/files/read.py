"""File reading methods IR generation."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.io.files.common import (
    allocate_and_read_full_file,
    allocate_and_read_line,
    allocate_and_read_char
)
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc


def generate_read(module: ir.Module) -> None:
    """Generate IR for file.read() -> string"""
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)

    fn_ty = ir.FunctionType(string_struct_type, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_read")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    result = allocate_and_read_full_file(module, builder, file_ptr)

    builder.ret(result)


def generate_readln(module: ir.Module) -> None:
    """Generate IR for file.readln() -> string"""
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)

    fn_ty = ir.FunctionType(string_struct_type, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_readln")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    result = allocate_and_read_line(module, builder, file_ptr)

    builder.ret(result)


def generate_readch(module: ir.Module) -> None:
    """Generate IR for file.readch() -> string"""
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)

    fn_ty = ir.FunctionType(string_struct_type, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_readch")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    result = allocate_and_read_char(module, builder, file_ptr)

    builder.ret(result)


def generate_lines(module: ir.Module) -> None:
    """Generate IR for file.lines() -> Iterator<string>"""
    malloc_fn = declare_malloc(module)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()
    string_fat_ptr = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)

    iterator_struct_ty = ir.LiteralStructType([i32, i32, string_fat_ptr.as_pointer()])

    fn_ty = ir.FunctionType(iterator_struct_ty, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_lines")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    iterator_slot = builder.alloca(iterator_struct_ty, name="file_lines_iter")

    # For streaming file iterators, we need to store the FILE* pointer
    # Create a "fake" fat pointer by storing FILE* in field 0 and 0 in field 1
    # Allocate a fat pointer on the heap to store this
    fat_ptr_size = ir.Constant(i64, 16)  # Size of {i8*, i32} = 8 + 4 (with padding)
    file_ptr_storage = builder.call(malloc_fn, [fat_ptr_size], name="file_ptr_storage")
    file_ptr_storage_typed = builder.bitcast(file_ptr_storage, string_fat_ptr.as_pointer(), name="file_ptr_ptr")

    zero = ir.Constant(i32, 0)
    one = ir.Constant(i32, 1)
    field0_ptr = builder.gep(file_ptr_storage_typed, [zero, zero])
    builder.store(file_ptr, field0_ptr)

    field1_ptr = builder.gep(file_ptr_storage_typed, [zero, one])
    builder.store(zero, field1_ptr)

    index_ptr = builder.gep(iterator_slot, [zero, zero])
    builder.store(zero, index_ptr)

    length_ptr = builder.gep(iterator_slot, [zero, one])
    builder.store(ir.Constant(i32, -1), length_ptr)

    two = ir.Constant(i32, 2)
    data_ptr_ptr = builder.gep(iterator_slot, [zero, two])
    builder.store(file_ptr_storage_typed, data_ptr_ptr)

    result = builder.load(iterator_slot)
    builder.ret(result)
