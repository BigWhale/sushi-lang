"""
String Replace Operations

Implements the replace() method for fat pointer strings.
"""

import llvmlite.ir as ir
from stdlib.src.type_definitions import get_string_types
from stdlib.src.libc_declarations import declare_malloc, declare_memcpy


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
