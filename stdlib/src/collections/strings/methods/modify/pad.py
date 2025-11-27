"""
String Padding and Repeat Operations

Implements padding and repeat methods for fat pointer strings.
"""

import llvmlite.ir as ir
from stdlib.src.type_definitions import get_string_types
from stdlib.src.libc_declarations import declare_malloc, declare_memcpy
from ...intrinsics import declare_utf8_count_intrinsic
from ...common import build_string_struct


def emit_string_repeat(module: ir.Module) -> ir.Function:
    """Emit the string.repeat() method.

    Repeats the string n times, returning a new concatenated string.
    Returns empty string if n <= 0 or if original string is empty.

    Algorithm:
    1. Extract string data and size
    2. Handle edge cases: n <= 0 or empty string
    3. Calculate total_size = size * n
    4. Allocate buffer
    5. Loop n times copying the string
    6. Return new string

    Examples:
        "abc".repeat(3) -> "abcabcabc"
        "x".repeat(5) -> "xxxxx"
        "test".repeat(0) -> ""
        "".repeat(10) -> ""

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_repeat({ i8*, i32 } str, i32 n)
    """
    func_name = "string_repeat"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare external functions
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    # Function signature: { i8*, i32 } string_repeat({ i8*, i32 } str, i32 n)
    fn_ty = ir.FunctionType(string_type, [string_type, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "n"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    check_n = func.append_basic_block("check_n")
    check_size = func.append_basic_block("check_size")
    allocate_block = func.append_basic_block("allocate")
    loop_cond = func.append_basic_block("loop_cond")
    loop_body = func.append_basic_block("loop_body")
    loop_done = func.append_basic_block("loop_done")
    return_empty = func.append_basic_block("return_empty")

    # Entry: extract string data and size
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    builder.branch(check_n)

    # Check n: if n <= 0, return empty string
    builder = ir.IRBuilder(check_n)
    n_positive = builder.icmp_signed(">", func.args[1], ir.Constant(i32, 0), name="n_positive")
    builder.cbranch(n_positive, check_size, return_empty)

    # Check size: if size == 0, return empty string
    builder = ir.IRBuilder(check_size)
    size_nonzero = builder.icmp_unsigned(">", str_size, ir.Constant(i32, 0), name="size_nonzero")
    builder.cbranch(size_nonzero, allocate_block, return_empty)

    # Allocate: calculate total size and allocate buffer
    builder = ir.IRBuilder(allocate_block)
    total_size = builder.mul(str_size, func.args[1], name="total_size")

    # Allocate buffer
    total_size_i64 = builder.zext(total_size, i64, name="total_size_i64")
    result_data = builder.call(malloc, [total_size_i64], name="result_data")

    builder.branch(loop_cond)

    # Loop condition: i < n
    builder = ir.IRBuilder(loop_cond)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), allocate_block)

    continue_loop = builder.icmp_unsigned("<", i_phi, func.args[1], name="continue_loop")
    builder.cbranch(continue_loop, loop_body, loop_done)

    # Loop body: copy string at offset i * size
    builder = ir.IRBuilder(loop_body)
    offset = builder.mul(i_phi, str_size, name="offset")
    dest_ptr = builder.gep(result_data, [offset], name="dest_ptr")

    # Copy string data
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [dest_ptr, str_data, str_size, is_volatile])

    # Increment i
    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, loop_body)

    builder.branch(loop_cond)

    # Loop done: build and return result string
    builder = ir.IRBuilder(loop_done)
    result_string = build_string_struct(builder, string_type, result_data, total_size)
    builder.ret(result_string)

    # Return empty: return empty string { NULL, 0 }
    builder = ir.IRBuilder(return_empty)
    null_ptr = ir.Constant(i8_ptr, None)
    empty_string = build_string_struct(builder, string_type, null_ptr, ir.Constant(i32, 0))
    builder.ret(empty_string)

    return func


def emit_string_pad_left(module: ir.Module) -> ir.Function:
    """Emit the string.pad_left() method.

    Pads string to specified width by prepending padding character(s).
    Width is measured in UTF-8 characters. If the string is already >= width,
    returns a copy of the original string.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_pad_left({ i8*, i32 } str, i32 width, { i8*, i32 } pad_char)
    """
    func_name = "string_pad_left"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count = declare_utf8_count_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type, i32, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "width"
    func.args[2].name = "pad_char"

    entry_block = func.append_basic_block("entry")
    no_padding_block = func.append_basic_block("no_padding")
    do_padding_block = func.append_basic_block("do_padding")
    padding_loop_block = func.append_basic_block("padding_loop")
    padding_done_block = func.append_basic_block("padding_done")
    return_block = func.append_basic_block("return")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    width = func.args[1]
    pad_data = builder.extract_value(func.args[2], 0, name="pad_data")
    pad_size = builder.extract_value(func.args[2], 1, name="pad_size")

    current_chars = builder.call(utf8_count, [str_data, str_size], name="current_chars")
    needs_padding = builder.icmp_signed("<", current_chars, width, name="needs_padding")
    builder.cbranch(needs_padding, do_padding_block, no_padding_block)

    builder.position_at_end(no_padding_block)
    str_size_i64 = builder.zext(str_size, i64, name="str_size_i64")
    no_pad_copy = builder.call(malloc, [str_size_i64], name="no_pad_copy")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [no_pad_copy, str_data, str_size, is_volatile])
    no_pad_result = build_string_struct(builder, string_type, no_pad_copy, str_size)
    builder.branch(return_block)

    builder.position_at_end(do_padding_block)
    needed_chars = builder.sub(width, current_chars, name="needed_chars")
    pad_bytes = builder.mul(needed_chars, pad_size, name="pad_bytes")
    total_size = builder.add(pad_bytes, str_size, name="total_size")
    total_size_i64 = builder.zext(total_size, i64, name="total_size_i64")
    result_data = builder.call(malloc, [total_size_i64], name="result_data")

    idx_ptr = builder.alloca(i32, name="idx_ptr")
    builder.store(ir.Constant(i32, 0), idx_ptr)
    offset_ptr = builder.alloca(i32, name="offset_ptr")
    builder.store(ir.Constant(i32, 0), offset_ptr)
    builder.branch(padding_loop_block)

    builder.position_at_end(padding_loop_block)
    idx = builder.load(idx_ptr, name="idx")
    loop_continue = builder.icmp_signed("<", idx, needed_chars, name="loop_continue")

    padding_body_block = func.append_basic_block("padding_body")
    builder.cbranch(loop_continue, padding_body_block, padding_done_block)

    builder.position_at_end(padding_body_block)
    offset = builder.load(offset_ptr, name="offset")
    dest_ptr = builder.gep(result_data, [offset], name="dest_ptr")
    builder.call(memcpy, [dest_ptr, pad_data, pad_size, is_volatile])
    new_offset = builder.add(offset, pad_size, name="new_offset")
    builder.store(new_offset, offset_ptr)
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx_ptr)
    builder.branch(padding_loop_block)

    builder.position_at_end(padding_done_block)
    final_offset = builder.load(offset_ptr, name="final_offset")
    str_dest_ptr = builder.gep(result_data, [final_offset], name="str_dest_ptr")
    builder.call(memcpy, [str_dest_ptr, str_data, str_size, is_volatile])
    padded_result = build_string_struct(builder, string_type, result_data, total_size)
    builder.branch(return_block)

    builder.position_at_end(return_block)
    result_phi = builder.phi(string_type, name="result")
    result_phi.add_incoming(no_pad_result, no_padding_block)
    result_phi.add_incoming(padded_result, padding_done_block)
    builder.ret(result_phi)

    return func


def emit_string_pad_right(module: ir.Module) -> ir.Function:
    """Emit the string.pad_right() method.

    Pads string to specified width by appending padding character(s).
    Width is measured in UTF-8 characters. If the string is already >= width,
    returns a copy of the original string.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_pad_right({ i8*, i32 } str, i32 width, { i8*, i32 } pad_char)
    """
    func_name = "string_pad_right"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count = declare_utf8_count_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type, i32, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "width"
    func.args[2].name = "pad_char"

    entry_block = func.append_basic_block("entry")
    no_padding_block = func.append_basic_block("no_padding")
    do_padding_block = func.append_basic_block("do_padding")
    padding_loop_block = func.append_basic_block("padding_loop")
    return_block = func.append_basic_block("return")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    width = func.args[1]
    pad_data = builder.extract_value(func.args[2], 0, name="pad_data")
    pad_size = builder.extract_value(func.args[2], 1, name="pad_size")

    current_chars = builder.call(utf8_count, [str_data, str_size], name="current_chars")
    needs_padding = builder.icmp_signed("<", current_chars, width, name="needs_padding")
    builder.cbranch(needs_padding, do_padding_block, no_padding_block)

    builder.position_at_end(no_padding_block)
    str_size_i64 = builder.zext(str_size, i64, name="str_size_i64")
    no_pad_copy = builder.call(malloc, [str_size_i64], name="no_pad_copy")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [no_pad_copy, str_data, str_size, is_volatile])
    no_pad_result = build_string_struct(builder, string_type, no_pad_copy, str_size)
    builder.branch(return_block)

    builder.position_at_end(do_padding_block)
    needed_chars = builder.sub(width, current_chars, name="needed_chars")
    pad_bytes = builder.mul(needed_chars, pad_size, name="pad_bytes")
    total_size = builder.add(str_size, pad_bytes, name="total_size")
    total_size_i64 = builder.zext(total_size, i64, name="total_size_i64")
    result_data = builder.call(malloc, [total_size_i64], name="result_data")

    builder.call(memcpy, [result_data, str_data, str_size, is_volatile])

    idx_ptr = builder.alloca(i32, name="idx_ptr")
    builder.store(ir.Constant(i32, 0), idx_ptr)
    offset_ptr = builder.alloca(i32, name="offset_ptr")
    builder.store(str_size, offset_ptr)
    builder.branch(padding_loop_block)

    builder.position_at_end(padding_loop_block)
    idx = builder.load(idx_ptr, name="idx")
    loop_continue = builder.icmp_signed("<", idx, needed_chars, name="loop_continue")

    padding_body_block = func.append_basic_block("padding_body")
    padding_done_block = func.append_basic_block("padding_done")
    builder.cbranch(loop_continue, padding_body_block, padding_done_block)

    builder.position_at_end(padding_body_block)
    offset = builder.load(offset_ptr, name="offset")
    dest_ptr = builder.gep(result_data, [offset], name="dest_ptr")
    builder.call(memcpy, [dest_ptr, pad_data, pad_size, is_volatile])
    new_offset = builder.add(offset, pad_size, name="new_offset")
    builder.store(new_offset, offset_ptr)
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx_ptr)
    builder.branch(padding_loop_block)

    builder.position_at_end(padding_done_block)
    padded_result = build_string_struct(builder, string_type, result_data, total_size)
    builder.branch(return_block)

    builder.position_at_end(return_block)
    result_phi = builder.phi(string_type, name="result")
    result_phi.add_incoming(no_pad_result, no_padding_block)
    result_phi.add_incoming(padded_result, padding_done_block)
    builder.ret(result_phi)

    return func
