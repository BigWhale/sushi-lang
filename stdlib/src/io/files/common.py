"""
Common utilities for file I/O IR generation.

This module provides shared utilities used by all file method implementations,
including helper functions for string operations and file pointer management.
"""

import llvmlite.ir as ir
from stdlib.src.libc_declarations import (
    declare_malloc, declare_strlen, declare_fgetc,
    declare_fgets, declare_fread, declare_fwrite,
    declare_fclose, declare_realloc
)
from stdlib.src.string_helpers import cstr_to_fat_pointer
from stdlib.src.error_emission import emit_runtime_error


def allocate_and_read_line(
    module: ir.Module,
    builder: ir.IRBuilder,
    file_ptr: ir.Value
) -> ir.Value:
    """
    Allocate buffer and read one line from file using fgets.

    Returns fat pointer struct {i8*, i32} (with newline removed if present).
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = i8.as_pointer()

    # Declare required functions
    fgets_fn = declare_fgets(module)
    malloc_fn = declare_malloc(module)
    strlen_fn = declare_strlen(module)

    # Allocate buffer for the line (1024 bytes)
    buffer_size_i64 = ir.Constant(i64, 1024)
    buffer_size_i32 = ir.Constant(i32, 1024)
    buffer = builder.call(malloc_fn, [buffer_size_i64])

    # Call fgets(buffer, 1024, file_ptr)
    result = builder.call(fgets_fn, [buffer, buffer_size_i32, file_ptr])

    # Remove trailing newline if present
    strlen_result = builder.call(strlen_fn, [buffer])

    # Check if length > 0
    zero = ir.Constant(i32, 0)
    has_chars = builder.icmp_signed('>', strlen_result, zero)

    # Variable to store final length (may be decremented if newline is removed)
    final_length = builder.alloca(i32, name="final_length")
    builder.store(strlen_result, final_length)

    with builder.if_then(has_chars):
        # Get pointer to last character (length - 1)
        one = ir.Constant(i32, 1)
        last_index = builder.sub(strlen_result, one)
        last_char_ptr = builder.gep(buffer, [last_index])
        last_char = builder.load(last_char_ptr)

        # Check if it's a newline
        newline = ir.Constant(i8, ord('\n'))
        is_newline = builder.icmp_signed('==', last_char, newline)

        with builder.if_then(is_newline):
            # Replace newline with null terminator
            null_char = ir.Constant(i8, 0)
            builder.store(null_char, last_char_ptr)
            # Decrement final length
            builder.store(last_index, final_length)

    # Convert C string to fat pointer, passing pre-computed length
    final_len_val = builder.load(final_length)
    from stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    return cstr_to_fat_pointer_with_len(builder, buffer, final_len_val)


def allocate_and_read_char(
    module: ir.Module,
    builder: ir.IRBuilder,
    file_ptr: ir.Value
) -> ir.Value:
    """
    Allocate buffer and read one character from file using fgetc.

    Returns fat pointer struct {i8*, i32} (empty if EOF).
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # Declare required functions
    fgetc_fn = declare_fgetc(module)
    malloc_fn = declare_malloc(module)

    # Call fgetc(file_ptr)
    ch = builder.call(fgetc_fn, [file_ptr])

    # Check for EOF (fgetc returns -1 on EOF)
    eof_val = ir.Constant(i32, -1)
    is_eof = builder.icmp_signed('==', ch, eof_val)

    # Allocate space for result string (2 bytes: char + null)
    two_bytes = ir.Constant(i64, 2)
    buffer = builder.call(malloc_fn, [two_bytes])

    # Branch based on EOF check
    eof_block = builder.append_basic_block("readch_eof")
    char_block = builder.append_basic_block("readch_char")
    merge_block = builder.append_basic_block("readch_merge")

    builder.cbranch(is_eof, eof_block, char_block)

    # EOF path: Return empty string
    builder.position_at_end(eof_block)
    null_char = ir.Constant(i8, 0)
    builder.store(null_char, buffer)
    builder.branch(merge_block)

    # Character path: Store character and null terminator
    builder.position_at_end(char_block)
    ch_i8 = builder.trunc(ch, i8)
    zero = ir.Constant(i32, 0)
    one = ir.Constant(i32, 1)
    char_ptr = builder.gep(buffer, [zero])
    builder.store(ch_i8, char_ptr)
    null_ptr = builder.gep(buffer, [one])
    builder.store(null_char, null_ptr)
    builder.branch(merge_block)

    # Merge block: Convert C string to fat pointer and return
    builder.position_at_end(merge_block)
    return cstr_to_fat_pointer(module, builder, buffer)


def allocate_and_read_full_file(
    module: ir.Module,
    builder: ir.IRBuilder,
    file_ptr: ir.Value
) -> ir.Value:
    """
    Allocate buffer and read entire file contents character by character.

    Uses dynamic growth with realloc. Returns fat pointer struct {i8*, i32}.
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = i8.as_pointer()

    # Declare required functions
    fgetc_fn = declare_fgetc(module)
    malloc_fn = declare_malloc(module)
    realloc_fn = declare_realloc(module)

    # Start with initial buffer size (1024 bytes)
    initial_size = 1024
    capacity_ptr = builder.alloca(i32, name="read_capacity")
    length_ptr = builder.alloca(i32, name="read_length")

    # Allocate initial buffer
    initial_size_i64 = ir.Constant(i64, initial_size)
    initial_size_i32 = ir.Constant(i32, initial_size)
    buffer_ptr = builder.alloca(i8_ptr, name="read_buffer_ptr")
    initial_buffer = builder.call(malloc_fn, [initial_size_i64])
    builder.store(initial_buffer, buffer_ptr)
    builder.store(initial_size_i32, capacity_ptr)
    builder.store(ir.Constant(i32, 0), length_ptr)

    # Read loop
    loop_head = builder.append_basic_block("file_read_loop_head")
    loop_body = builder.append_basic_block("file_read_loop_body")
    loop_exit = builder.append_basic_block("file_read_loop_exit")

    builder.branch(loop_head)

    # Loop head: Read one character
    builder.position_at_end(loop_head)
    ch = builder.call(fgetc_fn, [file_ptr])

    # Check for EOF (fgetc returns -1 on EOF)
    eof_val = ir.Constant(i32, -1)
    is_eof = builder.icmp_signed('==', ch, eof_val)
    builder.cbranch(is_eof, loop_exit, loop_body)

    # Loop body: Store character and check capacity
    builder.position_at_end(loop_body)
    current_length = builder.load(length_ptr)
    current_capacity = builder.load(capacity_ptr)
    current_buffer = builder.load(buffer_ptr)

    # Check if we need to grow the buffer (+1 for null terminator)
    one = ir.Constant(i32, 1)
    needed_capacity = builder.add(current_length, one)
    needs_grow = builder.icmp_signed('>=', needed_capacity, current_capacity)

    grow_block = builder.append_basic_block("file_read_grow")
    store_block = builder.append_basic_block("file_read_store")
    builder.cbranch(needs_grow, grow_block, store_block)

    # Grow buffer (double capacity)
    builder.position_at_end(grow_block)
    two = ir.Constant(i32, 2)
    new_capacity = builder.mul(current_capacity, two)
    new_capacity_i64 = builder.zext(new_capacity, i64, name="new_capacity_i64")
    new_buffer = builder.call(realloc_fn, [current_buffer, new_capacity_i64])
    builder.store(new_buffer, buffer_ptr)
    builder.store(new_capacity, capacity_ptr)
    builder.branch(store_block)

    # Store character
    builder.position_at_end(store_block)
    final_buffer = builder.load(buffer_ptr)
    final_length = builder.load(length_ptr)
    char_ptr = builder.gep(final_buffer, [final_length])
    ch_i8 = builder.trunc(ch, i8)
    builder.store(ch_i8, char_ptr)

    # Increment length
    new_length = builder.add(final_length, one)
    builder.store(new_length, length_ptr)
    builder.branch(loop_head)

    # Loop exit: Add null terminator and convert to fat pointer
    builder.position_at_end(loop_exit)
    final_buffer = builder.load(buffer_ptr)
    final_length = builder.load(length_ptr)
    null_ptr = builder.gep(final_buffer, [final_length])
    null_char = ir.Constant(i8, 0)
    builder.store(null_char, null_ptr)

    # Convert C string to fat pointer and return
    return cstr_to_fat_pointer(module, builder, final_buffer)
