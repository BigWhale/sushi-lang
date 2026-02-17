"""
stdin module - Standard input stream methods.

This module implements IR generation for stdin methods:
- readln() -> string: Read one line from standard input
- read() -> string: Read all input until EOF
- read_bytes(i32) -> u8[]: Read n bytes from standard input
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import (
    declare_malloc, declare_free, declare_realloc,
    declare_getline, declare_fgetc, declare_fread
)
from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error
from sushi_lang.sushi_stdlib.src.io.stdio.common import declare_stdin_handle


def generate_stdin_readln(module: ir.Module) -> None:
    """Generate IR for stdin.readln() -> string.

    Uses POSIX getline() to read one line from stdin with dynamic buffer allocation.
    Handles lines of any length, strips trailing \\n and \\r\\n.
    Returns empty string on EOF.

    Args:
        module: The LLVM module to add the function to.
    """
    # Declare external functions
    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)
    getline_fn = declare_getline(module)
    stdin_handle = declare_stdin_handle(module)

    # Function signature: {ptr, i32} @sushi_stdin_readln()
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
    fn_ty = ir.FunctionType(string_struct_ty, [])
    func = ir.Function(module, fn_ty, name="sushi_stdin_readln")

    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # Allocate lineptr (i8*) and n (i64) for getline
    lineptr_alloca = builder.alloca(i8_ptr, name="lineptr")
    n_alloca = builder.alloca(i64, name="n")
    builder.store(ir.Constant(i8_ptr, None), lineptr_alloca)
    builder.store(ir.Constant(i64, 0), n_alloca)

    # Call getline(&lineptr, &n, stdin) -> ssize_t (i64)
    stdin_ptr = builder.load(stdin_handle, name="stdin")
    bytes_read = builder.call(getline_fn, [lineptr_alloca, n_alloca, stdin_ptr], name="bytes_read")

    # Check if getline returned < 0 (EOF or error)
    zero_i64 = ir.Constant(i64, 0)
    is_eof = builder.icmp_signed('<', bytes_read, zero_i64, name="is_eof")

    eof_block = builder.append_basic_block(name="eof")
    success_block = builder.append_basic_block(name="success")
    builder.cbranch(is_eof, eof_block, success_block)

    # EOF path: free getline buffer (if any), return empty string
    builder.position_at_end(eof_block)
    eof_lineptr = builder.load(lineptr_alloca, name="eof_lineptr")
    eof_null = ir.Constant(i8_ptr, None)
    eof_has_buf = builder.icmp_unsigned('!=', eof_lineptr, eof_null, name="eof_has_buf")
    eof_free_block = builder.append_basic_block(name="eof_free")
    eof_ret_block = builder.append_basic_block(name="eof_ret")
    builder.cbranch(eof_has_buf, eof_free_block, eof_ret_block)

    builder.position_at_end(eof_free_block)
    builder.call(free_fn, [eof_lineptr])
    builder.branch(eof_ret_block)

    builder.position_at_end(eof_ret_block)
    # Return empty string: malloc(1) with null terminator
    empty_buf = builder.call(malloc_fn, [ir.Constant(i64, 1)], name="empty_buf")
    builder.store(ir.Constant(i8, 0), empty_buf)
    from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    empty_fat = cstr_to_fat_pointer_with_len(builder, empty_buf, ir.Constant(i32, 0))
    builder.ret(empty_fat)

    # Success path: strip trailing \n and \r\n
    builder.position_at_end(success_block)
    lineptr_val = builder.load(lineptr_alloca, name="lineptr")

    # Truncate bytes_read from i64 to i32 for the fat pointer length
    len_i32 = builder.trunc(bytes_read, i32, name="len_i32")
    final_length = builder.alloca(i32, name="final_length")
    builder.store(len_i32, final_length)

    # Check for trailing \n
    one_i32 = ir.Constant(i32, 1)
    has_chars = builder.icmp_signed('>', len_i32, ir.Constant(i32, 0), name="has_chars")

    check_newline_block = builder.append_basic_block(name="check_newline")
    exit_block = builder.append_basic_block(name="exit")
    builder.cbranch(has_chars, check_newline_block, exit_block)

    # Check and strip trailing \n
    builder.position_at_end(check_newline_block)
    cur_len = builder.load(final_length, name="cur_len")
    last_idx = builder.sub(cur_len, one_i32, name="last_idx")
    last_ptr = builder.gep(lineptr_val, [last_idx], name="last_ptr")
    last_char = builder.load(last_ptr, name="last_char")
    newline = ir.Constant(i8, ord('\n'))
    is_newline = builder.icmp_signed('==', last_char, newline, name="is_newline")

    strip_newline_block = builder.append_basic_block(name="strip_newline")
    builder.cbranch(is_newline, strip_newline_block, exit_block)

    # Strip \n, then check for \r
    builder.position_at_end(strip_newline_block)
    null_byte = ir.Constant(i8, 0)
    builder.store(null_byte, last_ptr)
    new_len = builder.sub(cur_len, one_i32, name="new_len_no_lf")
    builder.store(new_len, final_length)

    # Check for \r (i.e., \r\n line ending)
    has_more = builder.icmp_signed('>', new_len, ir.Constant(i32, 0), name="has_more")
    check_cr_block = builder.append_basic_block(name="check_cr")
    builder.cbranch(has_more, check_cr_block, exit_block)

    builder.position_at_end(check_cr_block)
    cr_idx = builder.sub(new_len, one_i32, name="cr_idx")
    cr_ptr = builder.gep(lineptr_val, [cr_idx], name="cr_ptr")
    cr_char = builder.load(cr_ptr, name="cr_char")
    cr_byte = ir.Constant(i8, ord('\r'))
    is_cr = builder.icmp_signed('==', cr_char, cr_byte, name="is_cr")

    strip_cr_block = builder.append_basic_block(name="strip_cr")
    builder.cbranch(is_cr, strip_cr_block, exit_block)

    builder.position_at_end(strip_cr_block)
    builder.store(null_byte, cr_ptr)
    len_no_crlf = builder.sub(new_len, one_i32, name="new_len_no_crlf")
    builder.store(len_no_crlf, final_length)
    builder.branch(exit_block)

    # Exit: return fat pointer with getline's buffer
    builder.position_at_end(exit_block)
    final_len_val = builder.load(final_length, name="final_len")
    fat_ptr = cstr_to_fat_pointer_with_len(builder, lineptr_val, final_len_val)
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
