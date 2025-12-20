"""
String Reverse Operations

Implements the reverse() method for fat pointer strings.
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc, declare_memcpy
from ...intrinsics import declare_utf8_count_intrinsic, declare_utf8_byte_offset_intrinsic
from ...common import build_string_struct


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
