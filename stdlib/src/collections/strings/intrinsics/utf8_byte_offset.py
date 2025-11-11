"""
UTF-8 Byte Offset Intrinsic

Implements pure LLVM IR function to find the byte offset of the nth UTF-8 character
in a bounded byte range. This works with fat pointer string representation.

Algorithm:
- Iterate through bytes until we've seen 'char_index' characters
- Count characters by detecting non-continuation bytes
- UTF-8 continuation bytes have pattern 10xxxxxx (0x80-0xBF)
- Returns byte offset where the character starts, or -1 if out of bounds
"""

import llvmlite.ir as ir


def emit_utf8_byte_offset_intrinsic(module: ir.Module) -> ir.Function:
    """Emit the UTF-8 byte offset intrinsic function.

    This function finds the byte offset of the nth UTF-8 character by iterating
    through exactly 'size' bytes and counting non-continuation bytes until
    reaching the target character index.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 llvm_utf8_byte_offset(i8* data, i32 size, i32 char_index)
    """
    func_name = "llvm_utf8_byte_offset"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Types
    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()
    i32 = ir.IntType(32)
    i1 = ir.IntType(1)

    # Function signature: i32 llvm_utf8_byte_offset(i8* data, i32 size, i32 char_index)
    fn_ty = ir.FunctionType(i32, [i8_ptr, i32, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "data"
    func.args[1].name = "size"
    func.args[2].name = "char_index"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    loop_header = func.append_basic_block("loop_header")
    loop_body = func.append_basic_block("loop_body")
    found_char = func.append_basic_block("found_char")
    loop_continue = func.append_basic_block("loop_continue")
    loop_exit = func.append_basic_block("loop_exit")

    builder = ir.IRBuilder(entry_block)

    # Entry: Initialize character counter and byte index
    char_count = builder.alloca(i32, name="char_count")
    builder.store(ir.Constant(i32, 0), char_count)
    byte_idx = builder.alloca(i32, name="byte_idx")
    builder.store(ir.Constant(i32, 0), byte_idx)
    builder.branch(loop_header)

    # Loop header: Check if byte_idx < size
    builder.position_at_end(loop_header)
    byte_idx_val = builder.load(byte_idx, name="byte_idx_val")
    cond = builder.icmp_signed("<", byte_idx_val, func.args[1], name="loop_cond")
    builder.cbranch(cond, loop_body, loop_exit)

    # Loop body: Check if byte is NOT a continuation byte
    builder.position_at_end(loop_body)
    byte_idx_val = builder.load(byte_idx, name="byte_idx_val")

    # Get byte at current index
    byte_ptr = builder.gep(func.args[0], [byte_idx_val], name="byte_ptr")
    byte_val = builder.load(byte_ptr, name="byte_val")

    # Check if (byte & 0xC0) != 0x80
    # Continuation bytes: 10xxxxxx (0x80-0xBF)
    # Start/ASCII bytes: 0xxxxxxx, 110xxxxx, 1110xxxx, 11110xxx
    masked = builder.and_(byte_val, ir.Constant(i8, 0xC0), name="masked")
    is_continuation = builder.icmp_unsigned("==", masked, ir.Constant(i8, 0x80), name="is_continuation")
    is_start = builder.xor(is_continuation, ir.Constant(i1, 1), name="is_start")

    # If it's a start byte, check if we've reached the target character
    char_count_val = builder.load(char_count, name="char_count_val")
    target_reached = builder.icmp_signed("==", char_count_val, func.args[2], name="target_reached")
    found = builder.and_(is_start, target_reached, name="found")

    # Create a block to increment character counter
    inc_char_block = func.append_basic_block("inc_char")

    # Branch: if found, return current byte offset; otherwise check if we need to increment
    builder.cbranch(found, found_char, inc_char_block)

    # Increment character counter if this is a start byte
    builder.position_at_end(inc_char_block)
    char_count_val = builder.load(char_count, name="char_count_val")
    incremented = builder.add(char_count_val, ir.Constant(i32, 1), name="incremented")
    new_count = builder.select(is_start, incremented, char_count_val, name="new_count")
    builder.store(new_count, char_count)
    builder.branch(loop_continue)

    # Found character: return current byte offset
    builder.position_at_end(found_char)
    byte_idx_val = builder.load(byte_idx, name="byte_idx_val")
    builder.ret(byte_idx_val)

    # Loop continue: Increment byte index
    builder.position_at_end(loop_continue)
    byte_idx_val = builder.load(byte_idx, name="byte_idx_val")
    next_idx = builder.add(byte_idx_val, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, byte_idx)
    builder.branch(loop_header)

    # Loop exit: Check if we found the character at the end
    builder.position_at_end(loop_exit)
    char_count_val = builder.load(char_count, name="char_count_val")
    target_reached = builder.icmp_signed("==", char_count_val, func.args[2], name="target_reached_exit")

    # If target reached at end (exact match), return size; otherwise return -1
    byte_idx_val = builder.load(byte_idx, name="byte_idx_val")
    result = builder.select(target_reached, byte_idx_val, ir.Constant(i32, -1), name="result")
    builder.ret(result)

    return func
