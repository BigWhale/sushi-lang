"""
String Search Operations

Implements string search methods for fat pointer strings:
- starts_with(): Check if string starts with prefix
- ends_with(): Check if string ends with suffix
- contains(): Check if string contains substring
- find(): Find first occurrence of substring (returns Maybe<i32>)
- find_last(): Find last occurrence of substring (returns Maybe<i32>)
- count(): Count non-overlapping occurrences of substring

All methods are byte-based (UTF-8 safe but unaware).
find() and find_last() return UTF-8 character indices.
"""

import llvmlite.ir as ir
from ..intrinsics import declare_utf8_count_intrinsic
from stdlib.src.type_definitions import get_string_types


def emit_string_starts_with(module: ir.Module) -> ir.Function:
    """Emit the string.starts_with() method.

    Checks if a string starts with a given prefix.
    Returns true if the prefix matches the beginning of the string.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i8 string_starts_with({ i8*, i32 } str, { i8*, i32 } prefix)
    """
    func_name = "string_starts_with"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i8 string_starts_with({ i8*, i32 } str, { i8*, i32 } prefix)
    fn_ty = ir.FunctionType(i8, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "prefix"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    loop_cond_block = func.append_basic_block("loop_cond")
    loop_body_block = func.append_basic_block("loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")

    # Entry block: extract data and sizes
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    prefix_data = builder.extract_value(func.args[1], 0, name="prefix_data")
    prefix_size = builder.extract_value(func.args[1], 1, name="prefix_size")
    builder.branch(size_check_block)

    # Size check: if prefix_size > str_size, return false
    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", prefix_size, str_size, name="size_ok")
    builder.cbranch(size_ok, loop_cond_block, mismatch_block)

    # Loop condition: i < prefix_size
    builder = ir.IRBuilder(loop_cond_block)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, prefix_size, name="cond")
    builder.cbranch(cond, loop_body_block, match_block)

    # Loop body: compare characters
    builder = ir.IRBuilder(loop_body_block)
    str_ptr = builder.gep(str_data, [i_phi], name="str_ptr")
    prefix_ptr = builder.gep(prefix_data, [i_phi], name="prefix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    prefix_ch = builder.load(prefix_ptr, name="prefix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, prefix_ch, name="chars_equal")

    # Increment i
    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, loop_body_block)

    builder.cbranch(chars_equal, loop_cond_block, mismatch_block)

    # Mismatch block: return false
    builder = ir.IRBuilder(mismatch_block)
    builder.ret(ir.Constant(i8, 0))

    # Match block: return true
    builder = ir.IRBuilder(match_block)
    builder.ret(ir.Constant(i8, 1))

    return func


def emit_string_ends_with(module: ir.Module) -> ir.Function:
    """Emit the string.ends_with() method.

    Checks if a string ends with a given suffix.
    Returns true if the suffix matches the end of the string.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i8 string_ends_with({ i8*, i32 } str, { i8*, i32 } suffix)
    """
    func_name = "string_ends_with"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i8 string_ends_with({ i8*, i32 } str, { i8*, i32 } suffix)
    fn_ty = ir.FunctionType(i8, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "suffix"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    loop_cond_block = func.append_basic_block("loop_cond")
    loop_body_block = func.append_basic_block("loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")

    # Entry block: extract data and sizes
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    suffix_data = builder.extract_value(func.args[1], 0, name="suffix_data")
    suffix_size = builder.extract_value(func.args[1], 1, name="suffix_size")
    builder.branch(size_check_block)

    # Size check: if suffix_size > str_size, return false
    # Also calculate offset: str_size - suffix_size
    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", suffix_size, str_size, name="size_ok")
    offset = builder.sub(str_size, suffix_size, name="offset")
    builder.cbranch(size_ok, loop_cond_block, mismatch_block)

    # Loop condition: i < suffix_size
    builder = ir.IRBuilder(loop_cond_block)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, suffix_size, name="cond")
    builder.cbranch(cond, loop_body_block, match_block)

    # Loop body: compare characters from end
    builder = ir.IRBuilder(loop_body_block)
    str_index = builder.add(offset, i_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    suffix_ptr = builder.gep(suffix_data, [i_phi], name="suffix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    suffix_ch = builder.load(suffix_ptr, name="suffix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, suffix_ch, name="chars_equal")

    # Increment i
    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, loop_body_block)

    builder.cbranch(chars_equal, loop_cond_block, mismatch_block)

    # Mismatch block: return false
    builder = ir.IRBuilder(mismatch_block)
    builder.ret(ir.Constant(i8, 0))

    # Match block: return true
    builder = ir.IRBuilder(match_block)
    builder.ret(ir.Constant(i8, 1))

    return func


def emit_string_contains(module: ir.Module) -> ir.Function:
    """Emit the string.contains() method.

    Checks if a string contains a given substring.
    Returns true if the substring is found anywhere in the string.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i8 string_contains({ i8*, i32 } str, { i8*, i32 } needle)
    """
    func_name = "string_contains"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i8 string_contains({ i8*, i32 } str, { i8*, i32 } needle)
    fn_ty = ir.FunctionType(i8, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "needle"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    empty_needle_check = func.append_basic_block("empty_needle_check")
    size_check_block = func.append_basic_block("size_check")
    outer_loop_cond = func.append_basic_block("outer_loop_cond")
    outer_loop_body = func.append_basic_block("outer_loop_body")
    inner_loop_cond = func.append_basic_block("inner_loop_cond")
    inner_loop_body = func.append_basic_block("inner_loop_body")
    inner_loop_mismatch = func.append_basic_block("inner_loop_mismatch")
    found_block = func.append_basic_block("found")
    not_found_block = func.append_basic_block("not_found")

    # Entry block: extract data and sizes
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    needle_data = builder.extract_value(func.args[1], 0, name="needle_data")
    needle_size = builder.extract_value(func.args[1], 1, name="needle_size")
    builder.branch(empty_needle_check)

    # Empty needle check: empty needle is always found
    builder = ir.IRBuilder(empty_needle_check)
    is_empty = builder.icmp_unsigned("==", needle_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, found_block, size_check_block)

    # Size check: if needle_size > str_size, return false
    # Calculate max_start: str_size - needle_size
    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", needle_size, str_size, name="size_ok")
    max_start = builder.sub(str_size, needle_size, name="max_start")
    builder.cbranch(size_ok, outer_loop_cond, not_found_block)

    # Outer loop: iterate through possible start positions
    builder = ir.IRBuilder(outer_loop_cond)
    pos_phi = builder.phi(i32, name="pos")
    pos_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    pos_ok = builder.icmp_unsigned("<=", pos_phi, max_start, name="pos_ok")
    builder.cbranch(pos_ok, outer_loop_body, not_found_block)

    # Outer loop body: start inner comparison
    builder = ir.IRBuilder(outer_loop_body)
    builder.branch(inner_loop_cond)

    # Inner loop: compare needle at current position
    builder = ir.IRBuilder(inner_loop_cond)
    j_phi = builder.phi(i32, name="j")
    j_phi.add_incoming(ir.Constant(i32, 0), outer_loop_body)
    j_ok = builder.icmp_unsigned("<", j_phi, needle_size, name="j_ok")
    builder.cbranch(j_ok, inner_loop_body, found_block)

    # Inner loop body: compare characters
    builder = ir.IRBuilder(inner_loop_body)
    str_index = builder.add(pos_phi, j_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    needle_ptr = builder.gep(needle_data, [j_phi], name="needle_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    needle_ch = builder.load(needle_ptr, name="needle_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, needle_ch, name="chars_equal")

    # Increment j
    j_next = builder.add(j_phi, ir.Constant(i32, 1), name="j_next")
    j_phi.add_incoming(j_next, inner_loop_body)

    builder.cbranch(chars_equal, inner_loop_cond, inner_loop_mismatch)

    # Inner loop mismatch: try next position
    builder = ir.IRBuilder(inner_loop_mismatch)
    pos_next = builder.add(pos_phi, ir.Constant(i32, 1), name="pos_next")
    pos_phi.add_incoming(pos_next, inner_loop_mismatch)
    builder.branch(outer_loop_cond)

    # Found block: return true
    builder = ir.IRBuilder(found_block)
    builder.ret(ir.Constant(i8, 1))

    # Not found block: return false
    builder = ir.IRBuilder(not_found_block)
    builder.ret(ir.Constant(i8, 0))

    return func


def emit_string_find(module: ir.Module) -> ir.Function:
    """Emit the string.find() method.

    Finds the first occurrence of a substring and returns its UTF-8 character index.
    Returns Maybe.Some(index) if found, Maybe.None() if not found.

    Note: The index is in UTF-8 characters, not bytes!

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8, i32 } string_find({ i8*, i32 } str, { i8*, i32 } needle)
        Returns Maybe<i32> represented as { i8 tag, i32 value }
    """
    func_name = "string_find"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()
    # Maybe<i32> uses the standard enum layout: {i32 tag, [4 x i8] data}
    # The i32 value is packed into the 4-byte data array
    i8_array_4 = ir.ArrayType(i8, 4)
    maybe_type = ir.LiteralStructType([i32, i8_array_4])

    # Declare utf8_count intrinsic
    utf8_count = declare_utf8_count_intrinsic(module)

    # Function signature: {i32, [4 x i8]} string_find({ i8*, i32 } str, { i8*, i32 } needle)
    fn_ty = ir.FunctionType(maybe_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "needle"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    empty_needle_check = func.append_basic_block("empty_needle_check")
    size_check_block = func.append_basic_block("size_check")
    outer_loop_cond = func.append_basic_block("outer_loop_cond")
    outer_loop_body = func.append_basic_block("outer_loop_body")
    inner_loop_cond = func.append_basic_block("inner_loop_cond")
    inner_loop_body = func.append_basic_block("inner_loop_body")
    inner_loop_mismatch = func.append_basic_block("inner_loop_mismatch")
    found_block = func.append_basic_block("found")
    not_found_block = func.append_basic_block("not_found")

    # Entry block: extract data and sizes
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    needle_data = builder.extract_value(func.args[1], 0, name="needle_data")
    needle_size = builder.extract_value(func.args[1], 1, name="needle_size")
    builder.branch(empty_needle_check)

    # Empty needle check: empty needle found at position 0
    builder = ir.IRBuilder(empty_needle_check)
    is_empty = builder.icmp_unsigned("==", needle_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, found_block, size_check_block)

    # Size check: if needle_size > str_size, return None
    # Calculate max_start: str_size - needle_size
    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", needle_size, str_size, name="size_ok")
    max_start = builder.sub(str_size, needle_size, name="max_start")
    builder.cbranch(size_ok, outer_loop_cond, not_found_block)

    # Outer loop: iterate through possible start positions (in bytes)
    builder = ir.IRBuilder(outer_loop_cond)
    pos_phi = builder.phi(i32, name="pos")
    pos_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    pos_ok = builder.icmp_unsigned("<=", pos_phi, max_start, name="pos_ok")
    builder.cbranch(pos_ok, outer_loop_body, not_found_block)

    # Outer loop body: start inner comparison
    builder = ir.IRBuilder(outer_loop_body)
    builder.branch(inner_loop_cond)

    # Inner loop: compare needle at current position
    builder = ir.IRBuilder(inner_loop_cond)
    j_phi = builder.phi(i32, name="j")
    j_phi.add_incoming(ir.Constant(i32, 0), outer_loop_body)
    j_ok = builder.icmp_unsigned("<", j_phi, needle_size, name="j_ok")
    builder.cbranch(j_ok, inner_loop_body, found_block)

    # Inner loop body: compare characters
    builder = ir.IRBuilder(inner_loop_body)
    str_index = builder.add(pos_phi, j_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    needle_ptr = builder.gep(needle_data, [j_phi], name="needle_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    needle_ch = builder.load(needle_ptr, name="needle_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, needle_ch, name="chars_equal")

    # Increment j
    j_next = builder.add(j_phi, ir.Constant(i32, 1), name="j_next")
    j_phi.add_incoming(j_next, inner_loop_body)

    builder.cbranch(chars_equal, inner_loop_cond, inner_loop_mismatch)

    # Inner loop mismatch: try next position
    builder = ir.IRBuilder(inner_loop_mismatch)
    pos_next = builder.add(pos_phi, ir.Constant(i32, 1), name="pos_next")
    pos_phi.add_incoming(pos_next, inner_loop_mismatch)
    builder.branch(outer_loop_cond)

    # Found block: convert byte position to character index, return Some(char_index)
    builder = ir.IRBuilder(found_block)
    found_pos_phi = builder.phi(i32, name="found_pos")
    found_pos_phi.add_incoming(ir.Constant(i32, 0), empty_needle_check)
    found_pos_phi.add_incoming(pos_phi, inner_loop_cond)

    # Convert byte offset to character index using utf8_count
    char_index = builder.call(utf8_count, [str_data, found_pos_phi], name="char_index")

    # Build Maybe.Some(char_index): tag = 0 (Some variant), data = packed i32
    # Enum layout: {i32 tag, [4 x i8] data}
    undef_maybe = ir.Constant(maybe_type, ir.Undefined)
    maybe_with_tag = builder.insert_value(undef_maybe, ir.Constant(i32, 0), 0, name="maybe_some_tag")

    # Pack the i32 value into the [4 x i8] data field
    temp_alloca = builder.alloca(i8_array_4, name="data_temp")
    data_ptr_i8 = builder.bitcast(temp_alloca, i8_ptr, name="data_ptr_i8")
    data_ptr_i32 = builder.bitcast(data_ptr_i8, ir.PointerType(i32), name="data_ptr_i32")
    builder.store(char_index, data_ptr_i32)
    packed_data = builder.load(temp_alloca, name="packed_data")
    maybe_complete = builder.insert_value(maybe_with_tag, packed_data, 1, name="maybe_some_data")
    builder.ret(maybe_complete)

    # Not found block: return Maybe.None(): tag = 1 (None variant)
    builder = ir.IRBuilder(not_found_block)
    undef_maybe_none = ir.Constant(maybe_type, ir.Undefined)
    maybe_none_with_tag = builder.insert_value(undef_maybe_none, ir.Constant(i32, 1), 0, name="maybe_none_tag")
    # Data field doesn't matter for None variant, but we need to fill it
    zero_data = ir.Constant(i8_array_4, [0, 0, 0, 0])
    maybe_none_complete = builder.insert_value(maybe_none_with_tag, zero_data, 1, name="maybe_none_data")
    builder.ret(maybe_none_complete)

    return func


def emit_string_count(module: ir.Module) -> ir.Function:
    """Emit the string.count() method.

    Counts non-overlapping occurrences of a substring in a string.
    Returns 0 if needle is empty or not found.

    Algorithm:
    - Similar to contains() but accumulates count instead of returning on first match
    - On match: increment count, skip past match (pos += needle_size)
    - On mismatch: try next position (pos++)

    Examples:
        "hello".count("l") -> 2
        "aaa".count("aa") -> 1 (non-overlapping)
        "test".count("x") -> 0
        "".count("x") -> 0
        "test".count("") -> 0

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 string_count({ i8*, i32 } str, { i8*, i32 } needle)
    """
    func_name = "string_count"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i32 string_count({ i8*, i32 } str, { i8*, i32 } needle)
    fn_ty = ir.FunctionType(i32, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "needle"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    empty_needle_check = func.append_basic_block("empty_needle_check")
    size_check_block = func.append_basic_block("size_check")
    outer_loop_cond = func.append_basic_block("outer_loop_cond")
    outer_loop_body = func.append_basic_block("outer_loop_body")
    inner_loop_cond = func.append_basic_block("inner_loop_cond")
    inner_loop_body = func.append_basic_block("inner_loop_body")
    inner_loop_match = func.append_basic_block("inner_loop_match")
    inner_loop_mismatch = func.append_basic_block("inner_loop_mismatch")
    return_count = func.append_basic_block("return_count")

    # Entry block: extract data and sizes
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    needle_data = builder.extract_value(func.args[1], 0, name="needle_data")
    needle_size = builder.extract_value(func.args[1], 1, name="needle_size")
    builder.branch(empty_needle_check)

    # Empty needle check: return 0 for empty needle
    builder = ir.IRBuilder(empty_needle_check)
    is_empty = builder.icmp_unsigned("==", needle_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, return_count, size_check_block)

    # Size check: if needle_size > str_size, return 0
    # Calculate max_start: str_size - needle_size
    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", needle_size, str_size, name="size_ok")
    max_start = builder.sub(str_size, needle_size, name="max_start")
    builder.cbranch(size_ok, outer_loop_cond, return_count)

    # Outer loop: iterate through possible start positions
    builder = ir.IRBuilder(outer_loop_cond)
    pos_phi = builder.phi(i32, name="pos")
    count_phi = builder.phi(i32, name="count")
    pos_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    count_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    pos_ok = builder.icmp_unsigned("<=", pos_phi, max_start, name="pos_ok")
    builder.cbranch(pos_ok, outer_loop_body, return_count)

    # Outer loop body: start inner comparison
    builder = ir.IRBuilder(outer_loop_body)
    builder.branch(inner_loop_cond)

    # Inner loop: compare needle at current position
    builder = ir.IRBuilder(inner_loop_cond)
    j_phi = builder.phi(i32, name="j")
    j_phi.add_incoming(ir.Constant(i32, 0), outer_loop_body)
    j_ok = builder.icmp_unsigned("<", j_phi, needle_size, name="j_ok")
    builder.cbranch(j_ok, inner_loop_body, inner_loop_match)

    # Inner loop body: compare characters
    builder = ir.IRBuilder(inner_loop_body)
    str_index = builder.add(pos_phi, j_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    needle_ptr = builder.gep(needle_data, [j_phi], name="needle_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    needle_ch = builder.load(needle_ptr, name="needle_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, needle_ch, name="chars_equal")

    # Increment j
    j_next = builder.add(j_phi, ir.Constant(i32, 1), name="j_next")
    j_phi.add_incoming(j_next, inner_loop_body)

    builder.cbranch(chars_equal, inner_loop_cond, inner_loop_mismatch)

    # Inner loop match: found a match, increment count and skip past match
    builder = ir.IRBuilder(inner_loop_match)
    count_incremented = builder.add(count_phi, ir.Constant(i32, 1), name="count_incremented")
    pos_skip = builder.add(pos_phi, needle_size, name="pos_skip")
    pos_phi.add_incoming(pos_skip, inner_loop_match)
    count_phi.add_incoming(count_incremented, inner_loop_match)
    builder.branch(outer_loop_cond)

    # Inner loop mismatch: try next position
    builder = ir.IRBuilder(inner_loop_mismatch)
    pos_next = builder.add(pos_phi, ir.Constant(i32, 1), name="pos_next")
    pos_phi.add_incoming(pos_next, inner_loop_mismatch)
    count_phi.add_incoming(count_phi, inner_loop_mismatch)
    builder.branch(outer_loop_cond)

    # Return count block
    builder = ir.IRBuilder(return_count)
    final_count_phi = builder.phi(i32, name="final_count")
    final_count_phi.add_incoming(ir.Constant(i32, 0), empty_needle_check)
    final_count_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    final_count_phi.add_incoming(count_phi, outer_loop_cond)
    builder.ret(final_count_phi)

    return func


def emit_string_find_last(module: ir.Module) -> ir.Function:
    """Emit the string.find_last() method.

    Finds the last occurrence of a substring and returns its UTF-8 character index.
    Returns Maybe.Some(index) if found, Maybe.None() if not found.

    Searches backwards from the end of the string for efficiency.

    Note: The index is in UTF-8 characters, not bytes!

    Examples:
        "hello world".find_last("o") -> Some(7)
        "test".find_last("x") -> None()
        "".find_last("x") -> None()

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i32, [4 x i8] } string_find_last({ i8*, i32 } str, { i8*, i32 } needle)
        Returns Maybe<i32> represented as { i32 tag, [4 x i8] data }
    """
    func_name = "string_find_last"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()
    # Maybe<i32> uses the standard enum layout: {i32 tag, [4 x i8] data}
    # The i32 value is packed into the 4-byte data array
    i8_array_4 = ir.ArrayType(i8, 4)
    maybe_type = ir.LiteralStructType([i32, i8_array_4])

    # Declare utf8_count intrinsic
    utf8_count = declare_utf8_count_intrinsic(module)

    # Function signature: {i32, [4 x i8]} string_find_last({ i8*, i32 } str, { i8*, i32 } needle)
    fn_ty = ir.FunctionType(maybe_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "needle"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    empty_needle_check = func.append_basic_block("empty_needle_check")
    size_check_block = func.append_basic_block("size_check")
    outer_loop_cond = func.append_basic_block("outer_loop_cond")
    outer_loop_body = func.append_basic_block("outer_loop_body")
    inner_loop_cond = func.append_basic_block("inner_loop_cond")
    inner_loop_body = func.append_basic_block("inner_loop_body")
    inner_loop_mismatch = func.append_basic_block("inner_loop_mismatch")
    found_block = func.append_basic_block("found")
    not_found_block = func.append_basic_block("not_found")

    # Entry block: extract data and sizes
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    needle_data = builder.extract_value(func.args[1], 0, name="needle_data")
    needle_size = builder.extract_value(func.args[1], 1, name="needle_size")
    builder.branch(empty_needle_check)

    # Empty needle check: empty needle found at last position (str_size in bytes, need char count)
    builder = ir.IRBuilder(empty_needle_check)
    is_empty = builder.icmp_unsigned("==", needle_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, found_block, size_check_block)

    # Size check: if needle_size > str_size, return None
    # Calculate max_start: str_size - needle_size
    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", needle_size, str_size, name="size_ok")
    max_start = builder.sub(str_size, needle_size, name="max_start")
    builder.cbranch(size_ok, outer_loop_cond, not_found_block)

    # Outer loop: iterate backwards from max_start down to 0 (in bytes)
    builder = ir.IRBuilder(outer_loop_cond)
    pos_phi = builder.phi(i32, name="pos")
    pos_phi.add_incoming(max_start, size_check_block)
    # Continue while pos >= 0 (using signed comparison to detect when we go below 0)
    pos_ok = builder.icmp_signed(">=", pos_phi, ir.Constant(i32, 0), name="pos_ok")
    builder.cbranch(pos_ok, outer_loop_body, not_found_block)

    # Outer loop body: start inner comparison
    builder = ir.IRBuilder(outer_loop_body)
    builder.branch(inner_loop_cond)

    # Inner loop: compare needle at current position
    builder = ir.IRBuilder(inner_loop_cond)
    j_phi = builder.phi(i32, name="j")
    j_phi.add_incoming(ir.Constant(i32, 0), outer_loop_body)
    j_ok = builder.icmp_unsigned("<", j_phi, needle_size, name="j_ok")
    builder.cbranch(j_ok, inner_loop_body, found_block)

    # Inner loop body: compare characters
    builder = ir.IRBuilder(inner_loop_body)
    str_index = builder.add(pos_phi, j_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    needle_ptr = builder.gep(needle_data, [j_phi], name="needle_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    needle_ch = builder.load(needle_ptr, name="needle_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, needle_ch, name="chars_equal")

    # Increment j
    j_next = builder.add(j_phi, ir.Constant(i32, 1), name="j_next")
    j_phi.add_incoming(j_next, inner_loop_body)

    builder.cbranch(chars_equal, inner_loop_cond, inner_loop_mismatch)

    # Inner loop mismatch: try previous position (decrement)
    builder = ir.IRBuilder(inner_loop_mismatch)
    pos_prev = builder.sub(pos_phi, ir.Constant(i32, 1), name="pos_prev")
    pos_phi.add_incoming(pos_prev, inner_loop_mismatch)
    builder.branch(outer_loop_cond)

    # Found block: convert byte position to character index, return Some(char_index)
    builder = ir.IRBuilder(found_block)
    found_pos_phi = builder.phi(i32, name="found_pos")
    # Empty needle case: return character count (end of string)
    found_pos_phi.add_incoming(str_size, empty_needle_check)
    # Normal match case: use the byte position where match was found
    found_pos_phi.add_incoming(pos_phi, inner_loop_cond)

    # Convert byte offset to character index using utf8_count
    char_index = builder.call(utf8_count, [str_data, found_pos_phi], name="char_index")

    # Build Maybe.Some(char_index): tag = 0 (Some variant), data = packed i32
    # Enum layout: {i32 tag, [4 x i8] data}
    undef_maybe = ir.Constant(maybe_type, ir.Undefined)
    maybe_with_tag = builder.insert_value(undef_maybe, ir.Constant(i32, 0), 0, name="maybe_some_tag")

    # Pack the i32 value into the [4 x i8] data field
    temp_alloca = builder.alloca(i8_array_4, name="data_temp")
    data_ptr_i8 = builder.bitcast(temp_alloca, i8_ptr, name="data_ptr_i8")
    data_ptr_i32 = builder.bitcast(data_ptr_i8, ir.PointerType(i32), name="data_ptr_i32")
    builder.store(char_index, data_ptr_i32)
    packed_data = builder.load(temp_alloca, name="packed_data")
    maybe_complete = builder.insert_value(maybe_with_tag, packed_data, 1, name="maybe_some_data")
    builder.ret(maybe_complete)

    # Not found block: return Maybe.None(): tag = 1 (None variant)
    builder = ir.IRBuilder(not_found_block)
    undef_maybe_none = ir.Constant(maybe_type, ir.Undefined)
    maybe_none_with_tag = builder.insert_value(undef_maybe_none, ir.Constant(i32, 1), 0, name="maybe_none_tag")
    # Data field doesn't matter for None variant, but we need to fill it
    zero_data = ir.Constant(i8_array_4, [0, 0, 0, 0])
    maybe_none_complete = builder.insert_value(maybe_none_with_tag, zero_data, 1, name="maybe_none_data")
    builder.ret(maybe_none_complete)

    return func
