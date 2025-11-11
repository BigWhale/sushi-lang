"""
Conversion Operations for Strings

Implements conversion methods that transform strings into other data types:
- to_bytes(): Converts string to u8[] dynamic array
- split(): Splits string into string[] array (Phase 8)
"""

import llvmlite.ir as ir
from ..common import declare_malloc, declare_memcpy, build_string_struct
from stdlib.src.type_definitions import get_string_types


def emit_string_to_bytes(module: ir.Module) -> ir.Function:
    """Emit the string.to_bytes() method.

    Converts a string to a dynamic u8 array by copying the byte data.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i32 len, i32 cap, u8* data } string_to_bytes({ i8*, i32 } str)
    """
    func_name = "string_to_bytes"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()
    dyn_array_type = ir.LiteralStructType([i32, i32, i8_ptr])  # {i32 len, i32 cap, u8* data}

    # Declare external functions
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    # Function signature: { i32, i32, u8* } string_to_bytes({ i8*, i32 } str)
    fn_ty = ir.FunctionType(dyn_array_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create entry block
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract data pointer and size from fat pointer
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Allocate memory for the byte array
    size_i64 = builder.zext(size, i64, name="size_i64")
    byte_data = builder.call(malloc, [size_i64], name="byte_data")

    # Copy string bytes to array using llvm.memcpy intrinsic
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [byte_data, data, size, is_volatile])

    # Build dynamic array struct: {i32 len, i32 cap, u8* data}
    # len = cap = size (exact fit, no extra capacity)
    undef_struct = ir.Constant(dyn_array_type, ir.Undefined)
    struct_with_len = builder.insert_value(undef_struct, size, 0, name="struct_with_len")
    struct_with_cap = builder.insert_value(struct_with_len, size, 1, name="struct_with_cap")
    struct_complete = builder.insert_value(struct_with_cap, byte_data, 2, name="result")

    builder.ret(struct_complete)

    return func


def emit_string_split(module: ir.Module) -> ir.Function:
    """Emit the string.split() method.

    Splits a string into an array of strings by a delimiter.

    Algorithm:
    1. Handle edge case: empty delimiter -> return array with original string
    2. Count occurrences of delimiter in string
    3. Allocate string[] array with (count + 1) elements
    4. Find each delimiter occurrence and extract substrings between them
    5. Store each substring in the array

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i32 len, i32 cap, {i8*,i32}* data } string_split({ i8*, i32 } str, { i8*, i32 } delim)
    """
    func_name = "string_split"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()
    i1 = ir.IntType(1)
    string_ptr = string_type.as_pointer()
    dyn_array_type = ir.LiteralStructType([i32, i32, string_ptr])  # {i32 len, i32 cap, string* data}

    # Declare external functions
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    # Function signature: { i32, i32, string* } string_split({ i8*, i32 } str, { i8*, i32 } delim)
    fn_ty = ir.FunctionType(dyn_array_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "delim"

    # Create basic blocks
    entry_block = func.append_basic_block("entry")
    empty_delim_block = func.append_basic_block("empty_delim")
    normal_split_block = func.append_basic_block("normal_split")
    count_loop_block = func.append_basic_block("count_loop")
    count_check_block = func.append_basic_block("count_check")
    count_match_block = func.append_basic_block("count_match")
    count_continue_block = func.append_basic_block("count_continue")
    count_done_block = func.append_basic_block("count_done")
    split_loop_block = func.append_basic_block("split_loop")
    split_check_block = func.append_basic_block("split_check")
    split_match_block = func.append_basic_block("split_match")
    split_continue_block = func.append_basic_block("split_continue")
    split_done_block = func.append_basic_block("split_done")
    return_block = func.append_basic_block("return")

    # Entry block: Extract string and delimiter data
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    delim_data = builder.extract_value(func.args[1], 0, name="delim_data")
    delim_size = builder.extract_value(func.args[1], 1, name="delim_size")

    # Check if delimiter is empty
    delim_is_empty = builder.icmp_signed("==", delim_size, ir.Constant(i32, 0), name="delim_is_empty")
    builder.cbranch(delim_is_empty, empty_delim_block, normal_split_block)

    # Empty delimiter case: return array with single element (original string)
    builder.position_at_end(empty_delim_block)
    one_elem = ir.Constant(i32, 1)
    string_size = ir.Constant(i64, 16)  # sizeof({i8*, i32}) = 16 bytes (8 + 4 + padding)
    array_data_empty = builder.call(malloc, [string_size], name="array_data_empty")
    array_data_empty_typed = builder.bitcast(array_data_empty, string_ptr, name="array_data_empty_typed")
    builder.store(func.args[0], array_data_empty_typed)

    undef_empty = ir.Constant(dyn_array_type, ir.Undefined)
    struct_empty_len = builder.insert_value(undef_empty, one_elem, 0, name="struct_empty_len")
    struct_empty_cap = builder.insert_value(struct_empty_len, one_elem, 1, name="struct_empty_cap")
    result_empty = builder.insert_value(struct_empty_cap, array_data_empty_typed, 2, name="result_empty")
    builder.branch(return_block)

    # Normal split: Count delimiter occurrences
    builder.position_at_end(normal_split_block)
    count_ptr = builder.alloca(i32, name="count_ptr")
    builder.store(ir.Constant(i32, 0), count_ptr)
    pos_ptr = builder.alloca(i32, name="pos_ptr")
    builder.store(ir.Constant(i32, 0), pos_ptr)
    builder.branch(count_loop_block)

    # Count loop: iterate through string looking for delimiter
    builder.position_at_end(count_loop_block)
    pos = builder.load(pos_ptr, name="pos")
    remaining = builder.sub(str_size, pos, name="remaining")
    can_fit = builder.icmp_signed(">=", remaining, delim_size, name="can_fit")
    builder.cbranch(can_fit, count_check_block, count_done_block)

    # Check if delimiter matches at current position
    builder.position_at_end(count_check_block)
    match_ptr = builder.alloca(i1, name="match_ptr")
    builder.store(ir.Constant(i1, 1), match_ptr)

    cmp_idx_ptr = builder.alloca(i32, name="cmp_idx_ptr")
    builder.store(ir.Constant(i32, 0), cmp_idx_ptr)

    # Inner loop to compare delimiter bytes
    cmp_loop_block = func.append_basic_block("cmp_loop")
    cmp_body_block = func.append_basic_block("cmp_body")
    cmp_done_block = func.append_basic_block("cmp_done")

    builder.branch(cmp_loop_block)

    builder.position_at_end(cmp_loop_block)
    cmp_idx = builder.load(cmp_idx_ptr, name="cmp_idx")
    cmp_done = builder.icmp_signed("<", cmp_idx, delim_size, name="cmp_not_done")
    builder.cbranch(cmp_done, cmp_body_block, cmp_done_block)

    builder.position_at_end(cmp_body_block)
    str_idx = builder.add(pos, cmp_idx, name="str_idx")
    str_byte_ptr = builder.gep(str_data, [str_idx], name="str_byte_ptr")
    str_byte = builder.load(str_byte_ptr, name="str_byte")
    delim_byte_ptr = builder.gep(delim_data, [cmp_idx], name="delim_byte_ptr")
    delim_byte = builder.load(delim_byte_ptr, name="delim_byte")
    bytes_match = builder.icmp_signed("==", str_byte, delim_byte, name="bytes_match")

    # If mismatch, set match to false
    not_match = builder.select(bytes_match, ir.Constant(i1, 1), ir.Constant(i1, 0), name="not_match")
    current_match = builder.load(match_ptr, name="current_match")
    new_match = builder.and_(current_match, not_match, name="new_match")
    builder.store(new_match, match_ptr)

    next_cmp_idx = builder.add(cmp_idx, ir.Constant(i32, 1), name="next_cmp_idx")
    builder.store(next_cmp_idx, cmp_idx_ptr)
    builder.branch(cmp_loop_block)

    builder.position_at_end(cmp_done_block)
    final_match = builder.load(match_ptr, name="final_match")
    builder.cbranch(final_match, count_match_block, count_continue_block)

    # Match found: increment count, skip delimiter
    builder.position_at_end(count_match_block)
    count = builder.load(count_ptr, name="count")
    new_count = builder.add(count, ir.Constant(i32, 1), name="new_count")
    builder.store(new_count, count_ptr)
    new_pos = builder.add(pos, delim_size, name="new_pos")
    builder.store(new_pos, pos_ptr)
    builder.branch(count_loop_block)

    # No match: advance by 1 byte
    builder.position_at_end(count_continue_block)
    next_pos = builder.add(pos, ir.Constant(i32, 1), name="next_pos")
    builder.store(next_pos, pos_ptr)
    builder.branch(count_loop_block)

    # Count done: allocate array for (count + 1) strings
    builder.position_at_end(count_done_block)
    final_count = builder.load(count_ptr, name="final_count")
    num_strings = builder.add(final_count, ir.Constant(i32, 1), name="num_strings")

    # Allocate array: num_strings * 16 bytes (sizeof string struct)
    num_strings_i64 = builder.zext(num_strings, i64, name="num_strings_i64")
    array_bytes = builder.mul(num_strings_i64, string_size, name="array_bytes")
    array_data_raw = builder.call(malloc, [array_bytes], name="array_data_raw")
    array_data = builder.bitcast(array_data_raw, string_ptr, name="array_data")

    # Reset position for splitting
    builder.store(ir.Constant(i32, 0), pos_ptr)
    array_idx_ptr = builder.alloca(i32, name="array_idx_ptr")
    builder.store(ir.Constant(i32, 0), array_idx_ptr)
    start_ptr = builder.alloca(i32, name="start_ptr")
    builder.store(ir.Constant(i32, 0), start_ptr)
    builder.branch(split_loop_block)

    # Split loop: extract substrings
    builder.position_at_end(split_loop_block)
    pos2 = builder.load(pos_ptr, name="pos2")
    remaining2 = builder.sub(str_size, pos2, name="remaining2")
    can_fit2 = builder.icmp_signed(">=", remaining2, delim_size, name="can_fit2")
    builder.cbranch(can_fit2, split_check_block, split_done_block)

    # Check for delimiter match (similar to count loop)
    builder.position_at_end(split_check_block)
    match_ptr2 = builder.alloca(i1, name="match_ptr2")
    builder.store(ir.Constant(i1, 1), match_ptr2)
    cmp_idx_ptr2 = builder.alloca(i32, name="cmp_idx_ptr2")
    builder.store(ir.Constant(i32, 0), cmp_idx_ptr2)

    cmp_loop2_block = func.append_basic_block("cmp_loop2")
    cmp_body2_block = func.append_basic_block("cmp_body2")
    cmp_done2_block = func.append_basic_block("cmp_done2")

    builder.branch(cmp_loop2_block)

    builder.position_at_end(cmp_loop2_block)
    cmp_idx2 = builder.load(cmp_idx_ptr2, name="cmp_idx2")
    cmp_done2 = builder.icmp_signed("<", cmp_idx2, delim_size, name="cmp_not_done2")
    builder.cbranch(cmp_done2, cmp_body2_block, cmp_done2_block)

    builder.position_at_end(cmp_body2_block)
    str_idx2 = builder.add(pos2, cmp_idx2, name="str_idx2")
    str_byte_ptr2 = builder.gep(str_data, [str_idx2], name="str_byte_ptr2")
    str_byte2 = builder.load(str_byte_ptr2, name="str_byte2")
    delim_byte_ptr2 = builder.gep(delim_data, [cmp_idx2], name="delim_byte_ptr2")
    delim_byte2 = builder.load(delim_byte_ptr2, name="delim_byte2")
    bytes_match2 = builder.icmp_signed("==", str_byte2, delim_byte2, name="bytes_match2")

    not_match2 = builder.select(bytes_match2, ir.Constant(i1, 1), ir.Constant(i1, 0), name="not_match2")
    current_match2 = builder.load(match_ptr2, name="current_match2")
    new_match2 = builder.and_(current_match2, not_match2, name="new_match2")
    builder.store(new_match2, match_ptr2)

    next_cmp_idx2 = builder.add(cmp_idx2, ir.Constant(i32, 1), name="next_cmp_idx2")
    builder.store(next_cmp_idx2, cmp_idx_ptr2)
    builder.branch(cmp_loop2_block)

    builder.position_at_end(cmp_done2_block)
    final_match2 = builder.load(match_ptr2, name="final_match2")
    builder.cbranch(final_match2, split_match_block, split_continue_block)

    # Match found: extract substring from start to pos, store in array
    builder.position_at_end(split_match_block)
    start = builder.load(start_ptr, name="start")
    substr_size = builder.sub(pos2, start, name="substr_size")

    # Allocate substring data
    substr_size_i64 = builder.zext(substr_size, i64, name="substr_size_i64")
    substr_data_raw = builder.call(malloc, [substr_size_i64], name="substr_data_raw")

    # Copy substring bytes using llvm.memcpy intrinsic
    start_ptr_gep = builder.gep(str_data, [start], name="start_ptr_gep")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [substr_data_raw, start_ptr_gep, substr_size, is_volatile])

    # Build substring struct
    substr_complete = build_string_struct(builder, string_type, substr_data_raw, substr_size)

    # Store in array
    array_idx = builder.load(array_idx_ptr, name="array_idx")
    array_elem_ptr = builder.gep(array_data, [array_idx], name="array_elem_ptr")
    builder.store(substr_complete, array_elem_ptr)

    # Update indices
    next_array_idx = builder.add(array_idx, ir.Constant(i32, 1), name="next_array_idx")
    builder.store(next_array_idx, array_idx_ptr)
    new_pos2 = builder.add(pos2, delim_size, name="new_pos2")
    builder.store(new_pos2, pos_ptr)
    builder.store(new_pos2, start_ptr)
    builder.branch(split_loop_block)

    # No match: advance by 1
    builder.position_at_end(split_continue_block)
    next_pos2 = builder.add(pos2, ir.Constant(i32, 1), name="next_pos2")
    builder.store(next_pos2, pos_ptr)
    builder.branch(split_loop_block)

    # Split done: add final substring from last delimiter to end
    builder.position_at_end(split_done_block)
    final_start = builder.load(start_ptr, name="final_start")
    final_substr_size = builder.sub(str_size, final_start, name="final_substr_size")

    # Allocate final substring
    final_substr_size_i64 = builder.zext(final_substr_size, i64, name="final_substr_size_i64")
    final_substr_data_raw = builder.call(malloc, [final_substr_size_i64], name="final_substr_data_raw")

    # Copy final bytes using llvm.memcpy intrinsic
    final_start_ptr = builder.gep(str_data, [final_start], name="final_start_ptr")
    builder.call(memcpy, [final_substr_data_raw, final_start_ptr, final_substr_size, is_volatile])

    # Build final substring struct
    final_complete = build_string_struct(builder, string_type, final_substr_data_raw, final_substr_size)

    # Store final substring
    final_array_idx = builder.load(array_idx_ptr, name="final_array_idx")
    final_array_elem_ptr = builder.gep(array_data, [final_array_idx], name="final_array_elem_ptr")
    builder.store(final_complete, final_array_elem_ptr)

    # Build result array
    undef_result = ir.Constant(dyn_array_type, ir.Undefined)
    result_with_len = builder.insert_value(undef_result, num_strings, 0, name="result_with_len")
    result_with_cap = builder.insert_value(result_with_len, num_strings, 1, name="result_with_cap")
    result_normal = builder.insert_value(result_with_cap, array_data, 2, name="result_normal")
    builder.branch(return_block)

    # Return block: merge both paths
    builder.position_at_end(return_block)
    result_phi = builder.phi(dyn_array_type, name="result")
    result_phi.add_incoming(result_empty, empty_delim_block)
    result_phi.add_incoming(result_normal, split_done_block)
    builder.ret(result_phi)

    return func
