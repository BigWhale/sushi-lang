"""
stdin module - Standard input stream methods.

This module implements IR generation for stdin methods:
- readln() -> string: Read one line from standard input
- read() -> string: Read all input until EOF
- read_bytes(i32) -> u8[]: Read n bytes from standard input
"""

import llvmlite.ir as ir
from stdlib.src.libc_declarations import (
    declare_malloc, declare_strlen, declare_realloc,
    declare_fgets, declare_fgetc, declare_fread
)
from stdlib.src.string_helpers import cstr_to_fat_pointer
from stdlib.src.error_emission import emit_runtime_error
from stdlib.src.io.stdio.common import declare_stdin_handle


def generate_stdin_readln(module: ir.Module) -> None:
    """Generate IR for stdin.readln() -> string.

    Reads one line from stdin, removes trailing newline if present.
    Returns fat pointer struct {i8*, i32}.

    Args:
        module: The LLVM module to add the function to.
    """
    # Declare external functions
    malloc_fn = declare_malloc(module)
    fgets_fn = declare_fgets(module)
    strlen_fn = declare_strlen(module)
    stdin_handle = declare_stdin_handle(module)

    # Function signature: {ptr, i32} @sushi_stdin_readln()
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
    fn_ty = ir.FunctionType(string_struct_ty, [])
    func = ir.Function(module, fn_ty, name="sushi_stdin_readln")

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Common types
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # Allocate buffer for the line (1024 bytes)
    buffer_size_i64 = ir.Constant(i64, 1024)
    buffer_size_i32 = ir.Constant(i32, 1024)
    buffer = builder.call(malloc_fn, [buffer_size_i64], name="buffer")

    # Call fgets(buffer, 1024, stdin)
    stdin_ptr = builder.load(stdin_handle, name="stdin")
    result = builder.call(fgets_fn, [buffer, buffer_size_i32, stdin_ptr], name="fgets_result")

    # Get string length
    strlen_result = builder.call(strlen_fn, [buffer], name="len")

    # Check if length > 0
    zero = ir.Constant(i32, 0)
    has_chars = builder.icmp_signed('>', strlen_result, zero, name="has_chars")

    # Variable to store final length (may be decremented if newline is removed)
    final_length = builder.alloca(i32, name="final_length")
    builder.store(strlen_result, final_length)

    # Create blocks for newline removal
    check_newline_block = builder.append_basic_block(name="check_newline")
    exit_block = builder.append_basic_block(name="exit")

    builder.cbranch(has_chars, check_newline_block, exit_block)

    # Check and remove trailing newline
    builder.position_at_end(check_newline_block)

    # Get pointer to last character (length - 1)
    one = ir.Constant(i32, 1)
    last_index = builder.sub(strlen_result, one, name="last_index")
    last_char_ptr = builder.gep(buffer, [last_index], name="last_char_ptr")
    last_char = builder.load(last_char_ptr, name="last_char")

    # Check if it's a newline
    newline = ir.Constant(i8, ord('\n'))
    is_newline = builder.icmp_signed('==', last_char, newline, name="is_newline")

    # Create blocks for newline removal
    remove_newline_block = builder.append_basic_block(name="remove_newline")
    builder.cbranch(is_newline, remove_newline_block, exit_block)

    # Remove newline
    builder.position_at_end(remove_newline_block)
    null_char = ir.Constant(i8, 0)
    builder.store(null_char, last_char_ptr)
    # Decrement final length
    builder.store(last_index, final_length)
    builder.branch(exit_block)

    # Exit block: convert C string to fat pointer, passing pre-computed length
    builder.position_at_end(exit_block)
    final_len_val = builder.load(final_length, name="final_len")
    from stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    fat_ptr = cstr_to_fat_pointer_with_len(builder, buffer, final_len_val)
    builder.ret(fat_ptr)


def generate_stdin_read(module: ir.Module) -> None:
    """Generate IR for stdin.read() -> string.

    Reads all input until EOF, dynamically growing the buffer as needed.
    Returns fat pointer struct {i8*, i32}.

    Args:
        module: The LLVM module to add the function to.
    """
    # Declare external functions
    malloc_fn = declare_malloc(module)
    realloc_fn = declare_realloc(module)
    fgetc_fn = declare_fgetc(module)
    stdin_handle = declare_stdin_handle(module)

    # Function signature: {ptr, i32} @sushi_stdin_read()
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
    fn_ty = ir.FunctionType(string_struct_ty, [])
    func = ir.Function(module, fn_ty, name="sushi_stdin_read")

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Common types
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # Constants
    initial_size = 1024
    zero = ir.Constant(i32, 0)
    one = ir.Constant(i32, 1)
    two = ir.Constant(i32, 2)
    eof_val = ir.Constant(i32, -1)

    # Allocate locals
    capacity_ptr = builder.alloca(i32, name="capacity")
    length_ptr = builder.alloca(i32, name="length")
    buffer_ptr = builder.alloca(i8_ptr, name="buffer_ptr")

    # Allocate initial buffer
    initial_size_i64 = ir.Constant(i64, initial_size)
    initial_size_i32 = ir.Constant(i32, initial_size)
    initial_buffer = builder.call(malloc_fn, [initial_size_i64], name="initial_buffer")
    builder.store(initial_buffer, buffer_ptr)
    builder.store(initial_size_i32, capacity_ptr)
    builder.store(zero, length_ptr)

    # Create loop blocks
    loop_head = builder.append_basic_block(name="loop_head")
    loop_body = builder.append_basic_block(name="loop_body")
    loop_exit = builder.append_basic_block(name="loop_exit")

    builder.branch(loop_head)

    # Loop head: Read one character
    builder.position_at_end(loop_head)
    stdin_ptr = builder.load(stdin_handle, name="stdin")
    ch = builder.call(fgetc_fn, [stdin_ptr], name="ch")

    # Check for EOF
    is_eof = builder.icmp_signed('==', ch, eof_val, name="is_eof")
    builder.cbranch(is_eof, loop_exit, loop_body)

    # Loop body: Store character and check capacity
    builder.position_at_end(loop_body)
    current_length = builder.load(length_ptr, name="current_length")
    current_capacity = builder.load(capacity_ptr, name="current_capacity")
    current_buffer = builder.load(buffer_ptr, name="current_buffer")

    # Check if we need to grow the buffer (+1 for null terminator)
    needed_capacity = builder.add(current_length, one, name="needed_capacity")
    needs_grow = builder.icmp_signed('>=', needed_capacity, current_capacity, name="needs_grow")

    grow_block = builder.append_basic_block(name="grow")
    store_block = builder.append_basic_block(name="store")
    builder.cbranch(needs_grow, grow_block, store_block)

    # Grow buffer (double capacity)
    builder.position_at_end(grow_block)
    new_capacity = builder.mul(current_capacity, two, name="new_capacity")
    new_capacity_i64 = builder.zext(new_capacity, i64, name="new_capacity_i64")
    new_buffer = builder.call(realloc_fn, [current_buffer, new_capacity_i64], name="new_buffer")
    builder.store(new_buffer, buffer_ptr)
    builder.store(new_capacity, capacity_ptr)
    builder.branch(store_block)

    # Store character
    builder.position_at_end(store_block)
    final_buffer = builder.load(buffer_ptr, name="final_buffer")
    final_length = builder.load(length_ptr, name="final_length")
    char_ptr = builder.gep(final_buffer, [final_length], name="char_ptr")
    ch_i8 = builder.trunc(ch, i8, name="ch_i8")
    builder.store(ch_i8, char_ptr)

    # Increment length
    new_length = builder.add(final_length, one, name="new_length")
    builder.store(new_length, length_ptr)
    builder.branch(loop_head)

    # Loop exit: Add null terminator and convert to fat pointer
    builder.position_at_end(loop_exit)
    final_buffer = builder.load(buffer_ptr, name="final_buffer")
    final_length = builder.load(length_ptr, name="final_length")
    null_ptr = builder.gep(final_buffer, [final_length], name="null_ptr")
    null_char = ir.Constant(i8, 0)
    builder.store(null_char, null_ptr)

    # Convert C string to fat pointer
    fat_ptr = cstr_to_fat_pointer(module, builder, final_buffer)
    builder.ret(fat_ptr)


def generate_stdin_read_bytes(module: ir.Module) -> None:
    """Generate IR for stdin.read_bytes(i32 count) -> u8[].

    Reads n bytes from stdin and returns a byte array.

    Args:
        module: The LLVM module to add the function to.
    """
    # Declare external functions
    malloc_fn = declare_malloc(module)
    fread_fn = declare_fread(module)
    stdin_handle = declare_stdin_handle(module)

    # Function signature: {i32, i32, ptr} @sushi_stdin_read_bytes(i32 %count)
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = i8.as_pointer()

    # Dynamic array struct: {i32 len, i32 cap, u8* data}
    array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])

    fn_ty = ir.FunctionType(array_struct_ty, [i32])
    func = ir.Function(module, fn_ty, name="sushi_stdin_read_bytes")
    func.args[0].name = "count"

    # Create entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    count_val = func.args[0]

    # Allocate buffer for reading bytes
    count_i64 = builder.zext(count_val, i64, name="count_i64")
    buffer = builder.call(malloc_fn, [count_i64], name="buffer")

    # Check for allocation failure
    null_ptr = ir.Constant(i8_ptr, None)
    is_null = builder.icmp_unsigned('==', buffer, null_ptr, name="is_null")

    fail_block = builder.append_basic_block(name="alloc_fail")
    continue_block = builder.append_basic_block(name="alloc_ok")
    builder.cbranch(is_null, fail_block, continue_block)

    # Allocation failed
    builder.position_at_end(fail_block)
    emit_runtime_error(module, builder, "RE2021", "Memory allocation failed")

    # Allocation succeeded - read bytes
    builder.position_at_end(continue_block)

    # Call fread(buffer, 1, count, stdin) - returns number of bytes actually read
    one_i64 = ir.Constant(i64, 1)
    stdin_ptr = builder.load(stdin_handle, name="stdin")
    bytes_read = builder.call(fread_fn, [buffer, one_i64, count_i64, stdin_ptr], name="bytes_read")

    # Truncate bytes_read from i64 to i32
    bytes_read_i32 = builder.trunc(bytes_read, i32, name="bytes_read_i32")

    # Create array struct
    array_struct = ir.Constant(array_struct_ty, ir.Undefined)
    array_struct = builder.insert_value(array_struct, bytes_read_i32, 0, name="with_len")
    array_struct = builder.insert_value(array_struct, count_val, 1, name="with_cap")
    array_struct = builder.insert_value(array_struct, buffer, 2, name="with_data")

    builder.ret(array_struct)
