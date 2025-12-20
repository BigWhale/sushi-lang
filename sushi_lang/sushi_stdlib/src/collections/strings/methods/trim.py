"""
Trim String Operations

Implements string trimming methods that remove ASCII whitespace:
- trim(): Remove whitespace from both ends
- tleft(): Remove whitespace from left end
- tright(): Remove whitespace from right end

ASCII whitespace: space (32), tab (9), newline (10), carriage return (13)
"""

import llvmlite.ir as ir
from ..intrinsics import declare_isspace_intrinsic
from ..common import declare_malloc, declare_memcpy, allocate_substring
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types


def emit_string_tleft(module: ir.Module) -> ir.Function:
    """Emit the string.tleft() method.

    Removes ASCII whitespace from the left end of the string.
    Returns a new string with leading whitespace removed.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_tleft({ i8*, i32 } str)
    """
    func_name = "string_tleft"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: { i8*, i32 } string_tleft({ i8*, i32 } str)
    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Declare functions
    isspace = declare_isspace_intrinsic(module)
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    # Create blocks
    entry_block = func.append_basic_block("entry")
    loop_cond_block = func.append_basic_block("loop_cond")
    loop_body_block = func.append_basic_block("loop_body")
    after_loop_block = func.append_basic_block("after_loop")

    # Entry block: extract data and size
    builder = ir.IRBuilder(entry_block)
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Initialize start index to 0
    start_ptr = builder.alloca(i32, name="start_ptr")
    builder.store(ir.Constant(i32, 0), start_ptr)
    builder.branch(loop_cond_block)

    # Loop condition: while start < size && isspace(data[start])
    builder = ir.IRBuilder(loop_cond_block)
    start_val = builder.load(start_ptr, name="start")
    is_in_bounds = builder.icmp_signed("<", start_val, size, name="is_in_bounds")

    # Load character and check if whitespace
    char_ptr = builder.gep(data, [start_val], name="char_ptr")
    char_byte = builder.load(char_ptr, name="char_byte")
    char_extended = builder.zext(char_byte, i32, name="char_extended")
    is_ws = builder.call(isspace, [char_extended], name="is_ws")
    is_ws_bool = builder.icmp_unsigned("!=", is_ws, ir.Constant(i8, 0), name="is_ws_bool")

    should_continue = builder.and_(is_in_bounds, is_ws_bool, name="should_continue")
    builder.cbranch(should_continue, loop_body_block, after_loop_block)

    # Loop body: increment start
    builder = ir.IRBuilder(loop_body_block)
    start_val_inc = builder.load(start_ptr, name="start_inc")
    next_start = builder.add(start_val_inc, ir.Constant(i32, 1), name="next_start")
    builder.store(next_start, start_ptr)
    builder.branch(loop_cond_block)

    # After loop: allocate new string and copy
    builder = ir.IRBuilder(after_loop_block)
    final_start = builder.load(start_ptr, name="final_start")
    new_size = builder.sub(size, final_start, name="new_size")

    # Allocate and copy substring
    result = allocate_substring(builder, malloc, memcpy, string_type, data, final_start, new_size, i32, i64)
    builder.ret(result)

    return func


def emit_string_tright(module: ir.Module) -> ir.Function:
    """Emit the string.tright() method.

    Removes ASCII whitespace from the right end of the string.
    Returns a new string with trailing whitespace removed.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_tright({ i8*, i32 } str)
    """
    func_name = "string_tright"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: { i8*, i32 } string_tright({ i8*, i32 } str)
    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Declare functions
    isspace = declare_isspace_intrinsic(module)
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    # Create blocks
    entry_block = func.append_basic_block("entry")
    loop_cond_block = func.append_basic_block("loop_cond")
    loop_body_block = func.append_basic_block("loop_body")
    after_loop_block = func.append_basic_block("after_loop")

    # Entry block: extract data and size
    builder = ir.IRBuilder(entry_block)
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Initialize end index to size
    end_ptr = builder.alloca(i32, name="end_ptr")
    builder.store(size, end_ptr)
    builder.branch(loop_cond_block)

    # Loop condition: while end > 0 && isspace(data[end - 1])
    builder = ir.IRBuilder(loop_cond_block)
    end_val = builder.load(end_ptr, name="end")
    is_positive = builder.icmp_signed(">", end_val, ir.Constant(i32, 0), name="is_positive")

    # Load character at end - 1 and check if whitespace
    char_idx = builder.sub(end_val, ir.Constant(i32, 1), name="char_idx")
    char_ptr = builder.gep(data, [char_idx], name="char_ptr")
    char_byte = builder.load(char_ptr, name="char_byte")
    char_extended = builder.zext(char_byte, i32, name="char_extended")
    is_ws = builder.call(isspace, [char_extended], name="is_ws")
    is_ws_bool = builder.icmp_unsigned("!=", is_ws, ir.Constant(i8, 0), name="is_ws_bool")

    should_continue = builder.and_(is_positive, is_ws_bool, name="should_continue")
    builder.cbranch(should_continue, loop_body_block, after_loop_block)

    # Loop body: decrement end
    builder = ir.IRBuilder(loop_body_block)
    end_val_dec = builder.load(end_ptr, name="end_dec")
    next_end = builder.sub(end_val_dec, ir.Constant(i32, 1), name="next_end")
    builder.store(next_end, end_ptr)
    builder.branch(loop_cond_block)

    # After loop: allocate new string and copy
    builder = ir.IRBuilder(after_loop_block)
    final_end = builder.load(end_ptr, name="final_end")

    # Allocate and copy substring (from start of string)
    zero_offset = ir.Constant(i32, 0)
    result = allocate_substring(builder, malloc, memcpy, string_type, data, zero_offset, final_end, i32, i64)
    builder.ret(result)

    return func


def emit_string_trim(module: ir.Module) -> ir.Function:
    """Emit the string.trim() method.

    Removes ASCII whitespace from both ends of the string.
    Returns a new string with leading and trailing whitespace removed.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_trim({ i8*, i32 } str)
    """
    func_name = "string_trim"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: { i8*, i32 } string_trim({ i8*, i32 } str)
    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Declare functions
    isspace = declare_isspace_intrinsic(module)
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    # Create blocks
    entry_block = func.append_basic_block("entry")
    left_loop_cond_block = func.append_basic_block("left_loop_cond")
    left_loop_body_block = func.append_basic_block("left_loop_body")
    right_loop_cond_block = func.append_basic_block("right_loop_cond")
    right_loop_body_block = func.append_basic_block("right_loop_body")
    after_loops_block = func.append_basic_block("after_loops")

    # Entry block: extract data and size
    builder = ir.IRBuilder(entry_block)
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Initialize start and end indices
    start_ptr = builder.alloca(i32, name="start_ptr")
    end_ptr = builder.alloca(i32, name="end_ptr")
    builder.store(ir.Constant(i32, 0), start_ptr)
    builder.store(size, end_ptr)
    builder.branch(left_loop_cond_block)

    # Left loop condition: while start < size && isspace(data[start])
    builder = ir.IRBuilder(left_loop_cond_block)
    start_val = builder.load(start_ptr, name="start")
    end_val_temp = builder.load(end_ptr, name="end_temp")
    is_in_bounds = builder.icmp_signed("<", start_val, end_val_temp, name="is_in_bounds")

    # Load character and check if whitespace
    char_ptr = builder.gep(data, [start_val], name="char_ptr")
    char_byte = builder.load(char_ptr, name="char_byte")
    char_extended = builder.zext(char_byte, i32, name="char_extended")
    is_ws = builder.call(isspace, [char_extended], name="is_ws")
    is_ws_bool = builder.icmp_unsigned("!=", is_ws, ir.Constant(i8, 0), name="is_ws_bool")

    should_continue_left = builder.and_(is_in_bounds, is_ws_bool, name="should_continue_left")
    builder.cbranch(should_continue_left, left_loop_body_block, right_loop_cond_block)

    # Left loop body: increment start
    builder = ir.IRBuilder(left_loop_body_block)
    start_val_inc = builder.load(start_ptr, name="start_inc")
    next_start = builder.add(start_val_inc, ir.Constant(i32, 1), name="next_start")
    builder.store(next_start, start_ptr)
    builder.branch(left_loop_cond_block)

    # Right loop condition: while end > start && isspace(data[end - 1])
    builder = ir.IRBuilder(right_loop_cond_block)
    end_val = builder.load(end_ptr, name="end")
    start_val_check = builder.load(start_ptr, name="start_check")
    is_positive = builder.icmp_signed(">", end_val, start_val_check, name="is_positive")

    # Load character at end - 1 and check if whitespace
    char_idx = builder.sub(end_val, ir.Constant(i32, 1), name="char_idx")
    char_ptr_right = builder.gep(data, [char_idx], name="char_ptr_right")
    char_byte_right = builder.load(char_ptr_right, name="char_byte_right")
    char_extended_right = builder.zext(char_byte_right, i32, name="char_extended_right")
    is_ws_right = builder.call(isspace, [char_extended_right], name="is_ws_right")
    is_ws_bool_right = builder.icmp_unsigned("!=", is_ws_right, ir.Constant(i8, 0), name="is_ws_bool_right")

    should_continue_right = builder.and_(is_positive, is_ws_bool_right, name="should_continue_right")
    builder.cbranch(should_continue_right, right_loop_body_block, after_loops_block)

    # Right loop body: decrement end
    builder = ir.IRBuilder(right_loop_body_block)
    end_val_dec = builder.load(end_ptr, name="end_dec")
    next_end = builder.sub(end_val_dec, ir.Constant(i32, 1), name="next_end")
    builder.store(next_end, end_ptr)
    builder.branch(right_loop_cond_block)

    # After loops: allocate new string and copy
    builder = ir.IRBuilder(after_loops_block)
    final_start = builder.load(start_ptr, name="final_start")
    final_end = builder.load(end_ptr, name="final_end")
    new_size = builder.sub(final_end, final_start, name="new_size")

    # Allocate and copy substring
    result = allocate_substring(builder, malloc, memcpy, string_type, data, final_start, new_size, i32, i64)
    builder.ret(result)

    return func
