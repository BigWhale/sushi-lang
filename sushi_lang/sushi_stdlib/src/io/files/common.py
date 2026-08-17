"""Common utilities for file I/O IR generation."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import (
    declare_malloc, declare_strlen, declare_fgetc,
    declare_fgets, declare_realloc
)
from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer


def allocate_and_read_line(
    module: ir.Module,
    builder: ir.IRBuilder,
    file_ptr: ir.Value
) -> ir.Value:
    """Allocate buffer and read one line from file using fgets."""
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    fgets_fn = declare_fgets(module)
    malloc_fn = declare_malloc(module)
    strlen_fn = declare_strlen(module)

    buffer_size_i64 = ir.Constant(i64, 1024)
    buffer_size_i32 = ir.Constant(i32, 1024)
    buffer = builder.call(malloc_fn, [buffer_size_i64])

    builder.call(fgets_fn, [buffer, buffer_size_i32, file_ptr])

    strlen_result = builder.call(strlen_fn, [buffer])

    zero = ir.Constant(i32, 0)
    has_chars = builder.icmp_signed('>', strlen_result, zero)

    final_length = builder.alloca(i32, name="final_length")
    builder.store(strlen_result, final_length)

    with builder.if_then(has_chars):
        one = ir.Constant(i32, 1)
        last_index = builder.sub(strlen_result, one)
        last_char_ptr = builder.gep(buffer, [last_index])
        last_char = builder.load(last_char_ptr)

        newline = ir.Constant(i8, ord('\n'))
        is_newline = builder.icmp_signed('==', last_char, newline)

        with builder.if_then(is_newline):
            null_char = ir.Constant(i8, 0)
            builder.store(null_char, last_char_ptr)
            builder.store(last_index, final_length)

    final_len_val = builder.load(final_length)
    from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    return cstr_to_fat_pointer_with_len(builder, buffer, final_len_val, owned=1)


def allocate_and_read_char(
    module: ir.Module,
    builder: ir.IRBuilder,
    file_ptr: ir.Value
) -> ir.Value:
    """Allocate buffer and read one character from file using fgetc."""
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    fgetc_fn = declare_fgetc(module)
    malloc_fn = declare_malloc(module)

    ch = builder.call(fgetc_fn, [file_ptr])

    eof_val = ir.Constant(i32, -1)
    is_eof = builder.icmp_signed('==', ch, eof_val)

    two_bytes = ir.Constant(i64, 2)
    buffer = builder.call(malloc_fn, [two_bytes])

    eof_block = builder.append_basic_block("readch_eof")
    char_block = builder.append_basic_block("readch_char")
    merge_block = builder.append_basic_block("readch_merge")

    builder.cbranch(is_eof, eof_block, char_block)

    builder.position_at_end(eof_block)
    null_char = ir.Constant(i8, 0)
    builder.store(null_char, buffer)
    builder.branch(merge_block)

    builder.position_at_end(char_block)
    ch_i8 = builder.trunc(ch, i8)
    zero = ir.Constant(i32, 0)
    one = ir.Constant(i32, 1)
    char_ptr = builder.gep(buffer, [zero])
    builder.store(ch_i8, char_ptr)
    null_ptr = builder.gep(buffer, [one])
    builder.store(null_char, null_ptr)
    builder.branch(merge_block)

    builder.position_at_end(merge_block)
    return cstr_to_fat_pointer(module, builder, buffer, owned=1)


def allocate_and_read_full_file(
    module: ir.Module,
    builder: ir.IRBuilder,
    file_ptr: ir.Value
) -> ir.Value:
    """Allocate buffer and read entire file contents character by character."""
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = i8.as_pointer()

    fgetc_fn = declare_fgetc(module)
    malloc_fn = declare_malloc(module)
    realloc_fn = declare_realloc(module)

    initial_size = 1024
    capacity_ptr = builder.alloca(i32, name="read_capacity")
    length_ptr = builder.alloca(i32, name="read_length")

    initial_size_i64 = ir.Constant(i64, initial_size)
    initial_size_i32 = ir.Constant(i32, initial_size)
    buffer_ptr = builder.alloca(i8_ptr, name="read_buffer_ptr")
    initial_buffer = builder.call(malloc_fn, [initial_size_i64])
    builder.store(initial_buffer, buffer_ptr)
    builder.store(initial_size_i32, capacity_ptr)
    builder.store(ir.Constant(i32, 0), length_ptr)

    loop_head = builder.append_basic_block("file_read_loop_head")
    loop_body = builder.append_basic_block("file_read_loop_body")
    loop_exit = builder.append_basic_block("file_read_loop_exit")

    builder.branch(loop_head)

    builder.position_at_end(loop_head)
    ch = builder.call(fgetc_fn, [file_ptr])

    eof_val = ir.Constant(i32, -1)
    is_eof = builder.icmp_signed('==', ch, eof_val)
    builder.cbranch(is_eof, loop_exit, loop_body)

    builder.position_at_end(loop_body)
    current_length = builder.load(length_ptr)
    current_capacity = builder.load(capacity_ptr)
    current_buffer = builder.load(buffer_ptr)

    one = ir.Constant(i32, 1)
    needed_capacity = builder.add(current_length, one)
    needs_grow = builder.icmp_signed('>=', needed_capacity, current_capacity)

    grow_block = builder.append_basic_block("file_read_grow")
    store_block = builder.append_basic_block("file_read_store")
    builder.cbranch(needs_grow, grow_block, store_block)

    builder.position_at_end(grow_block)
    two = ir.Constant(i32, 2)
    new_capacity = builder.mul(current_capacity, two)
    new_capacity_i64 = builder.zext(new_capacity, i64, name="new_capacity_i64")
    new_buffer = builder.call(realloc_fn, [current_buffer, new_capacity_i64])
    builder.store(new_buffer, buffer_ptr)
    builder.store(new_capacity, capacity_ptr)
    builder.branch(store_block)

    builder.position_at_end(store_block)
    final_buffer = builder.load(buffer_ptr)
    final_length = builder.load(length_ptr)
    char_ptr = builder.gep(final_buffer, [final_length])
    ch_i8 = builder.trunc(ch, i8)
    builder.store(ch_i8, char_ptr)

    new_length = builder.add(final_length, one)
    builder.store(new_length, length_ptr)
    builder.branch(loop_head)

    builder.position_at_end(loop_exit)
    final_buffer = builder.load(buffer_ptr)
    final_length = builder.load(length_ptr)
    null_ptr = builder.gep(final_buffer, [final_length])
    null_char = ir.Constant(i8, 0)
    builder.store(null_char, null_ptr)

    return cstr_to_fat_pointer(module, builder, final_buffer, owned=1)
