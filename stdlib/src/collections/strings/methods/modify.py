"""
String Modification Operations

Implements string modification methods for fat pointer strings:
- replace(): Replace all occurrences of a substring with another string
- reverse(): Reverse string preserving UTF-8 character boundaries
- repeat(): Repeat string n times
- pad_left(): Pad string to width by prepending padding character
- pad_right(): Pad string to width by appending padding character
- strip_prefix(): Remove prefix if present, otherwise return original
- strip_suffix(): Remove suffix if present, otherwise return original

All methods are UTF-8 aware where applicable.
"""

import llvmlite.ir as ir
from stdlib.src.type_definitions import get_string_types
from stdlib.src.libc_declarations import declare_malloc, declare_memcpy
from ..intrinsics import declare_utf8_count_intrinsic, declare_utf8_byte_offset_intrinsic
from ..common import build_string_struct


def emit_string_replace(module: ir.Module) -> ir.Function:
    """Emit the string.replace() method.

    Replaces all occurrences of a substring with another string.
    Returns a new string with all replacements made.

    Algorithm:
    1. Count occurrences of 'old' in the string
    2. Calculate result size: original_size - (old_len × count) + (new_len × count)
    3. Allocate result buffer
    4. Copy segments: for each match, copy prefix + new string, skip old string
    5. Return new string struct

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_replace({ i8*, i32 } str, { i8*, i32 } old, { i8*, i32 } new)
    """
    func_name = "string_replace"

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

    # Function signature: { i8*, i32 } string_replace({ i8*, i32 } str, { i8*, i32 } old, { i8*, i32 } new)
    fn_ty = ir.FunctionType(string_type, [string_type, string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "old"
    func.args[2].name = "new"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    empty_old_check = func.append_basic_block("empty_old_check")
    size_check_block = func.append_basic_block("size_check")

    # Counting phase blocks
    count_loop_cond = func.append_basic_block("count_loop_cond")
    count_loop_body = func.append_basic_block("count_loop_body")
    count_inner_cond = func.append_basic_block("count_inner_cond")
    count_inner_body = func.append_basic_block("count_inner_body")
    count_inner_match = func.append_basic_block("count_inner_match")
    count_inner_mismatch = func.append_basic_block("count_inner_mismatch")
    count_done = func.append_basic_block("count_done")

    # Allocation block
    alloc_result = func.append_basic_block("alloc_result")

    # Copy phase blocks
    copy_loop_cond = func.append_basic_block("copy_loop_cond")
    copy_loop_body = func.append_basic_block("copy_loop_body")
    copy_inner_cond = func.append_basic_block("copy_inner_cond")
    copy_inner_body = func.append_basic_block("copy_inner_body")
    copy_inner_match = func.append_basic_block("copy_inner_match")
    copy_inner_mismatch = func.append_basic_block("copy_inner_mismatch")
    copy_done = func.append_basic_block("copy_done")

    # Return original block
    return_original = func.append_basic_block("return_original")

    # Entry block: extract data and sizes
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    old_data = builder.extract_value(func.args[1], 0, name="old_data")
    old_size = builder.extract_value(func.args[1], 1, name="old_size")
    new_data = builder.extract_value(func.args[2], 0, name="new_data")
    new_size = builder.extract_value(func.args[2], 1, name="new_size")
    builder.branch(empty_old_check)

    # Empty old check: if old is empty, return original string
    builder = ir.IRBuilder(empty_old_check)
    is_empty = builder.icmp_unsigned("==", old_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, return_original, size_check_block)

    # Size check: if old_size > str_size, return original (no matches possible)
    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", old_size, str_size, name="size_ok")
    max_start = builder.sub(str_size, old_size, name="max_start")
    builder.cbranch(size_ok, count_loop_cond, return_original)

    # ========== COUNTING PHASE ==========
    # Count occurrences of 'old' in string

    # Count loop condition: pos <= max_start
    builder = ir.IRBuilder(count_loop_cond)
    count_pos_phi = builder.phi(i32, name="count_pos")
    count_phi = builder.phi(i32, name="count")
    count_pos_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    count_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    count_pos_ok = builder.icmp_unsigned("<=", count_pos_phi, max_start, name="count_pos_ok")
    builder.cbranch(count_pos_ok, count_loop_body, count_done)

    # Count loop body: start inner comparison
    builder = ir.IRBuilder(count_loop_body)
    builder.branch(count_inner_cond)

    # Count inner loop: compare 'old' at current position
    builder = ir.IRBuilder(count_inner_cond)
    count_j_phi = builder.phi(i32, name="count_j")
    count_j_phi.add_incoming(ir.Constant(i32, 0), count_loop_body)
    count_j_ok = builder.icmp_unsigned("<", count_j_phi, old_size, name="count_j_ok")
    builder.cbranch(count_j_ok, count_inner_body, count_inner_match)

    # Count inner body: compare characters
    builder = ir.IRBuilder(count_inner_body)
    count_str_index = builder.add(count_pos_phi, count_j_phi, name="count_str_index")
    count_str_ptr = builder.gep(str_data, [count_str_index], name="count_str_ptr")
    count_old_ptr = builder.gep(old_data, [count_j_phi], name="count_old_ptr")
    count_str_ch = builder.load(count_str_ptr, name="count_str_ch")
    count_old_ch = builder.load(count_old_ptr, name="count_old_ch")
    count_chars_equal = builder.icmp_unsigned("==", count_str_ch, count_old_ch, name="count_chars_equal")

    # Increment j
    count_j_next = builder.add(count_j_phi, ir.Constant(i32, 1), name="count_j_next")
    count_j_phi.add_incoming(count_j_next, count_inner_body)

    builder.cbranch(count_chars_equal, count_inner_cond, count_inner_mismatch)

    # Count inner match: found a match, increment count and skip past it
    builder = ir.IRBuilder(count_inner_match)
    count_incremented = builder.add(count_phi, ir.Constant(i32, 1), name="count_incremented")
    count_pos_skip = builder.add(count_pos_phi, old_size, name="count_pos_skip")
    count_pos_phi.add_incoming(count_pos_skip, count_inner_match)
    count_phi.add_incoming(count_incremented, count_inner_match)
    builder.branch(count_loop_cond)

    # Count inner mismatch: no match, move to next position
    builder = ir.IRBuilder(count_inner_mismatch)
    count_pos_next = builder.add(count_pos_phi, ir.Constant(i32, 1), name="count_pos_next")
    count_pos_phi.add_incoming(count_pos_next, count_inner_mismatch)
    count_phi.add_incoming(count_phi, count_inner_mismatch)
    builder.branch(count_loop_cond)

    # Count done: check if any matches found
    builder = ir.IRBuilder(count_done)
    has_matches = builder.icmp_unsigned(">", count_phi, ir.Constant(i32, 0), name="has_matches")
    builder.cbranch(has_matches, alloc_result, return_original)

    # ========== ALLOCATION PHASE ==========
    # Calculate result size and allocate buffer

    builder = ir.IRBuilder(alloc_result)
    # result_size = str_size - (old_size * count) + (new_size * count)
    old_total = builder.mul(old_size, count_phi, name="old_total")
    new_total = builder.mul(new_size, count_phi, name="new_total")
    size_without_old = builder.sub(str_size, old_total, name="size_without_old")
    result_size = builder.add(size_without_old, new_total, name="result_size")

    # Allocate result buffer
    result_size_i64 = builder.zext(result_size, i64, name="result_size_i64")
    result_data = builder.call(malloc, [result_size_i64], name="result_data")
    builder.branch(copy_loop_cond)

    # ========== COPY PHASE ==========
    # Copy string segments with replacements

    # Copy loop condition: src_pos < str_size
    builder = ir.IRBuilder(copy_loop_cond)
    copy_src_pos_phi = builder.phi(i32, name="copy_src_pos")
    copy_dst_pos_phi = builder.phi(i32, name="copy_dst_pos")
    copy_src_pos_phi.add_incoming(ir.Constant(i32, 0), alloc_result)
    copy_dst_pos_phi.add_incoming(ir.Constant(i32, 0), alloc_result)
    copy_src_ok = builder.icmp_unsigned("<", copy_src_pos_phi, str_size, name="copy_src_ok")
    builder.cbranch(copy_src_ok, copy_loop_body, copy_done)

    # Copy loop body: check if we can match 'old' at current position
    builder = ir.IRBuilder(copy_loop_body)
    can_match = builder.icmp_unsigned("<=", old_size, builder.sub(str_size, copy_src_pos_phi), name="can_match")
    builder.cbranch(can_match, copy_inner_cond, copy_inner_mismatch)

    # Copy inner loop: compare 'old' at current position
    builder = ir.IRBuilder(copy_inner_cond)
    copy_j_phi = builder.phi(i32, name="copy_j")
    copy_j_phi.add_incoming(ir.Constant(i32, 0), copy_loop_body)
    copy_j_ok = builder.icmp_unsigned("<", copy_j_phi, old_size, name="copy_j_ok")
    builder.cbranch(copy_j_ok, copy_inner_body, copy_inner_match)

    # Copy inner body: compare characters
    builder = ir.IRBuilder(copy_inner_body)
    copy_str_index = builder.add(copy_src_pos_phi, copy_j_phi, name="copy_str_index")
    copy_str_ptr = builder.gep(str_data, [copy_str_index], name="copy_str_ptr")
    copy_old_ptr = builder.gep(old_data, [copy_j_phi], name="copy_old_ptr")
    copy_str_ch = builder.load(copy_str_ptr, name="copy_str_ch")
    copy_old_ch = builder.load(copy_old_ptr, name="copy_old_ch")
    copy_chars_equal = builder.icmp_unsigned("==", copy_str_ch, copy_old_ch, name="copy_chars_equal")

    # Increment j
    copy_j_next = builder.add(copy_j_phi, ir.Constant(i32, 1), name="copy_j_next")
    copy_j_phi.add_incoming(copy_j_next, copy_inner_body)

    builder.cbranch(copy_chars_equal, copy_inner_cond, copy_inner_mismatch)

    # Copy inner match: found match, copy 'new' string and skip 'old'
    builder = ir.IRBuilder(copy_inner_match)
    # Copy new string to result
    copy_dst_ptr = builder.gep(result_data, [copy_dst_pos_phi], name="copy_dst_ptr")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [copy_dst_ptr, new_data, new_size, is_volatile])

    # Update positions
    copy_src_skip = builder.add(copy_src_pos_phi, old_size, name="copy_src_skip")
    copy_dst_advance = builder.add(copy_dst_pos_phi, new_size, name="copy_dst_advance")
    copy_src_pos_phi.add_incoming(copy_src_skip, copy_inner_match)
    copy_dst_pos_phi.add_incoming(copy_dst_advance, copy_inner_match)
    builder.branch(copy_loop_cond)

    # Copy inner mismatch: no match, copy one character from original
    builder = ir.IRBuilder(copy_inner_mismatch)
    copy_src_char_ptr = builder.gep(str_data, [copy_src_pos_phi], name="copy_src_char_ptr")
    copy_dst_char_ptr = builder.gep(result_data, [copy_dst_pos_phi], name="copy_dst_char_ptr")
    copy_char = builder.load(copy_src_char_ptr, name="copy_char")
    builder.store(copy_char, copy_dst_char_ptr)

    # Update positions
    copy_src_next = builder.add(copy_src_pos_phi, ir.Constant(i32, 1), name="copy_src_next")
    copy_dst_next = builder.add(copy_dst_pos_phi, ir.Constant(i32, 1), name="copy_dst_next")
    copy_src_pos_phi.add_incoming(copy_src_next, copy_inner_mismatch)
    copy_dst_pos_phi.add_incoming(copy_dst_next, copy_inner_mismatch)
    builder.branch(copy_loop_cond)

    # Copy done: build and return result string
    builder = ir.IRBuilder(copy_done)
    undef_result = ir.Constant(string_type, ir.Undefined)
    result_with_data = builder.insert_value(undef_result, result_data, 0, name="result_with_data")
    result_complete = builder.insert_value(result_with_data, result_size, 1, name="result_complete")
    builder.ret(result_complete)

    # Return original: no matches found or empty old string, return original
    builder = ir.IRBuilder(return_original)
    builder.ret(func.args[0])

    return func


def emit_string_reverse(module: ir.Module) -> ir.Function:
    """Emit the string.reverse() method.

    Reverses the string at UTF-8 character boundaries, preserving character integrity.
    Works correctly for ASCII and multi-byte UTF-8 characters.

    Algorithm:
    1. Count UTF-8 characters in the string
    2. Allocate buffer of same byte size
    3. Walk backwards through characters:
       - For each character from (count-1) down to 0:
         - Get byte offset of character i and i+1
         - Calculate character byte length
         - Copy character bytes to output buffer
    4. Return new string

    Example:
        "café" -> "éfac" (preserves the é as a 2-byte UTF-8 character)
        "hello" -> "olleh"

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_reverse({ i8*, i32 } str)
    """
    func_name = "string_reverse"

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
    utf8_count = declare_utf8_count_intrinsic(module)
    utf8_byte_offset = declare_utf8_byte_offset_intrinsic(module)

    # Function signature: { i8*, i32 } string_reverse({ i8*, i32 } str)
    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    empty_check = func.append_basic_block("empty_check")
    single_byte_check = func.append_basic_block("single_byte_check")
    reverse_block = func.append_basic_block("reverse")
    loop_cond = func.append_basic_block("loop_cond")
    loop_body = func.append_basic_block("loop_body")
    loop_done = func.append_basic_block("loop_done")
    return_original = func.append_basic_block("return_original")

    # Entry: extract string data and size
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    builder.branch(empty_check)

    # Empty check: if size == 0, return original
    builder = ir.IRBuilder(empty_check)
    is_empty = builder.icmp_unsigned("==", str_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, return_original, single_byte_check)

    # Single byte check: if size == 1, return original (optimization)
    builder = ir.IRBuilder(single_byte_check)
    is_single = builder.icmp_unsigned("==", str_size, ir.Constant(i32, 1), name="is_single")
    builder.cbranch(is_single, return_original, reverse_block)

    # Reverse block: count characters and allocate buffer
    builder = ir.IRBuilder(reverse_block)
    char_count = builder.call(utf8_count, [str_data, str_size], name="char_count")

    # Allocate buffer for result (same size as original)
    str_size_i64 = builder.zext(str_size, i64, name="str_size_i64")
    result_data = builder.call(malloc, [str_size_i64], name="result_data")

    # Calculate initial character index (char_count - 1)
    initial_char_index = builder.sub(char_count, ir.Constant(i32, 1), name="initial_char_index")

    # Branch to loop condition
    builder.branch(loop_cond)

    # Loop condition: char_index >= 0 (counting down from char_count - 1)
    builder = ir.IRBuilder(loop_cond)
    char_index_phi = builder.phi(i32, name="char_index")
    output_pos_phi = builder.phi(i32, name="output_pos")

    # Initialize PHI nodes
    char_index_phi.add_incoming(initial_char_index, reverse_block)
    output_pos_phi.add_incoming(ir.Constant(i32, 0), reverse_block)

    # Check if char_index >= 0
    continue_loop = builder.icmp_signed(">=", char_index_phi, ir.Constant(i32, 0), name="continue_loop")
    builder.cbranch(continue_loop, loop_body, loop_done)

    # Loop body: copy current character to output
    builder = ir.IRBuilder(loop_body)

    # Get byte offset for current character
    current_byte_offset = builder.call(
        utf8_byte_offset,
        [str_data, str_size, char_index_phi],
        name="current_byte_offset"
    )

    # Get byte offset for next character (char_index + 1)
    next_char_index = builder.add(char_index_phi, ir.Constant(i32, 1), name="next_char_index")
    next_byte_offset = builder.call(
        utf8_byte_offset,
        [str_data, str_size, next_char_index],
        name="next_byte_offset"
    )

    # Calculate character byte length
    char_byte_length = builder.sub(next_byte_offset, current_byte_offset, name="char_byte_length")

    # Get source and destination pointers
    src_ptr = builder.gep(str_data, [current_byte_offset], name="src_ptr")
    dst_ptr = builder.gep(result_data, [output_pos_phi], name="dst_ptr")

    # Copy character bytes
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [dst_ptr, src_ptr, char_byte_length, is_volatile])

    # Update indices
    char_index_next = builder.sub(char_index_phi, ir.Constant(i32, 1), name="char_index_next")
    output_pos_next = builder.add(output_pos_phi, char_byte_length, name="output_pos_next")

    char_index_phi.add_incoming(char_index_next, loop_body)
    output_pos_phi.add_incoming(output_pos_next, loop_body)

    builder.branch(loop_cond)

    # Loop done: build result string
    builder = ir.IRBuilder(loop_done)
    result_string = build_string_struct(builder, string_type, result_data, str_size)
    builder.ret(result_string)

    # Return original: return input unchanged
    builder = ir.IRBuilder(return_original)
    builder.ret(func.args[0])

    return func


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


def emit_string_strip_prefix(module: ir.Module) -> ir.Function:
    """Emit the string.strip_prefix() method.

    Removes prefix if present at the beginning of the string.
    If the string doesn't start with the prefix, returns the original string.

    Algorithm:
    1. Check if string starts with prefix (compare bytes)
    2. If matches: return substring from prefix.size to end
    3. If no match: return original string unchanged

    Examples:
        "hello".strip_prefix("he") -> "llo"
        "hello".strip_prefix("x") -> "hello"
        "test".strip_prefix("testing") -> "test"
        "".strip_prefix("x") -> ""

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_strip_prefix({ i8*, i32 } str, { i8*, i32 } prefix)
    """
    func_name = "string_strip_prefix"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    fn_ty = ir.FunctionType(string_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "prefix"

    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    compare_loop_cond = func.append_basic_block("compare_loop_cond")
    compare_loop_body = func.append_basic_block("compare_loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")
    return_original = func.append_basic_block("return_original")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    prefix_data = builder.extract_value(func.args[1], 0, name="prefix_data")
    prefix_size = builder.extract_value(func.args[1], 1, name="prefix_size")
    builder.branch(size_check_block)

    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", prefix_size, str_size, name="size_ok")
    builder.cbranch(size_ok, compare_loop_cond, return_original)

    builder = ir.IRBuilder(compare_loop_cond)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, prefix_size, name="cond")
    builder.cbranch(cond, compare_loop_body, match_block)

    builder = ir.IRBuilder(compare_loop_body)
    str_ptr = builder.gep(str_data, [i_phi], name="str_ptr")
    prefix_ptr = builder.gep(prefix_data, [i_phi], name="prefix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    prefix_ch = builder.load(prefix_ptr, name="prefix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, prefix_ch, name="chars_equal")

    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, compare_loop_body)

    builder.cbranch(chars_equal, compare_loop_cond, mismatch_block)

    builder = ir.IRBuilder(mismatch_block)
    builder.branch(return_original)

    builder = ir.IRBuilder(match_block)
    new_size = builder.sub(str_size, prefix_size, name="new_size")
    new_size_i64 = builder.zext(new_size, i64, name="new_size_i64")
    result_data = builder.call(malloc, [new_size_i64], name="result_data")

    new_data_ptr = builder.gep(str_data, [prefix_size], name="new_data_ptr")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [result_data, new_data_ptr, new_size, is_volatile])

    result = build_string_struct(builder, string_type, result_data, new_size)
    builder.ret(result)

    builder = ir.IRBuilder(return_original)
    builder.ret(func.args[0])

    return func


def emit_string_strip_suffix(module: ir.Module) -> ir.Function:
    """Emit the string.strip_suffix() method.

    Removes suffix if present at the end of the string.
    If the string doesn't end with the suffix, returns the original string.

    Algorithm:
    1. Check if string ends with suffix (compare bytes from end)
    2. If matches: return substring from 0 to (str_size - suffix_size)
    3. If no match: return original string unchanged

    Examples:
        "hello".strip_suffix("lo") -> "hel"
        "hello".strip_suffix("x") -> "hello"
        "test".strip_suffix("testing") -> "test"
        "".strip_suffix("x") -> ""

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_strip_suffix({ i8*, i32 } str, { i8*, i32 } suffix)
    """
    func_name = "string_strip_suffix"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    fn_ty = ir.FunctionType(string_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "suffix"

    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    compare_loop_cond = func.append_basic_block("compare_loop_cond")
    compare_loop_body = func.append_basic_block("compare_loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")
    return_original = func.append_basic_block("return_original")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    suffix_data = builder.extract_value(func.args[1], 0, name="suffix_data")
    suffix_size = builder.extract_value(func.args[1], 1, name="suffix_size")
    builder.branch(size_check_block)

    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", suffix_size, str_size, name="size_ok")
    offset = builder.sub(str_size, suffix_size, name="offset")
    builder.cbranch(size_ok, compare_loop_cond, return_original)

    builder = ir.IRBuilder(compare_loop_cond)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, suffix_size, name="cond")
    builder.cbranch(cond, compare_loop_body, match_block)

    builder = ir.IRBuilder(compare_loop_body)
    str_index = builder.add(offset, i_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    suffix_ptr = builder.gep(suffix_data, [i_phi], name="suffix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    suffix_ch = builder.load(suffix_ptr, name="suffix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, suffix_ch, name="chars_equal")

    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, compare_loop_body)

    builder.cbranch(chars_equal, compare_loop_cond, mismatch_block)

    builder = ir.IRBuilder(mismatch_block)
    builder.branch(return_original)

    builder = ir.IRBuilder(match_block)
    new_size = builder.sub(str_size, suffix_size, name="new_size")
    new_size_i64 = builder.zext(new_size, i64, name="new_size_i64")
    result_data = builder.call(malloc, [new_size_i64], name="result_data")

    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [result_data, str_data, new_size, is_volatile])

    result = build_string_struct(builder, string_type, result_data, new_size)
    builder.ret(result)

    builder = ir.IRBuilder(return_original)
    builder.ret(func.args[0])

    return func
