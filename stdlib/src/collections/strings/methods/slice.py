"""
String Slice Operations

Implements string slicing methods that work with fat pointer representation:
- ss(start, length): Byte-based substring (UTF-8 unaware, simpler)
- sleft(n): First n UTF-8 characters (UTF-8 aware)
- sright(n): Last n UTF-8 characters (UTF-8 aware)
- char_at(index): Single UTF-8 character at index (UTF-8 aware)
- s(start, end): Slice from character index start to end (UTF-8 aware)
"""

import llvmlite.ir as ir
from ..intrinsics import declare_utf8_count_intrinsic, declare_utf8_byte_offset_intrinsic
from ..common import declare_malloc, declare_memcpy, allocate_substring, build_string_struct
from stdlib.src.type_definitions import get_string_types


def emit_string_ss(module: ir.Module) -> ir.Function:
    """Emit the string.ss() method.

    Returns substring of 'length' bytes starting from byte index 'start'.
    This is byte-based (UTF-8 unaware) and simpler than character-based slicing.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_ss({ i8*, i32 } str, i32 start, i32 length)
    """
    func_name = "string_ss"

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

    # Function signature: { i8*, i32 } string_ss({ i8*, i32 } str, i32 start, i32 length)
    fn_ty = ir.FunctionType(string_type, [string_type, i32, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "start"
    func.args[2].name = "length"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract data and size from input string
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Clamp start to valid range [0, size]
    zero = ir.Constant(i32, 0)
    start_clamped = builder.select(
        builder.icmp_signed("<", func.args[1], zero),
        zero,
        func.args[1],
        name="start_clamped"
    )
    start_final = builder.select(
        builder.icmp_signed(">", start_clamped, size),
        size,
        start_clamped,
        name="start_final"
    )

    # Calculate maximum available length from start position
    remaining = builder.sub(size, start_final, name="remaining")

    # Clamp length to [0, remaining]
    length_clamped = builder.select(
        builder.icmp_signed("<", func.args[2], zero),
        zero,
        func.args[2],
        name="length_clamped"
    )
    length_final = builder.select(
        builder.icmp_signed(">", length_clamped, remaining),
        remaining,
        length_clamped,
        name="length_final"
    )

    # Allocate and copy substring
    result = allocate_substring(builder, malloc, memcpy, string_type, data, start_final, length_final, i32, i64)
    builder.ret(result)
    return func


def emit_string_sleft(module: ir.Module) -> ir.Function:
    """Emit the string.sleft() method.

    Returns first n UTF-8 characters.
    UTF-8 aware - uses utf8_byte_offset to find the byte boundary.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_sleft({ i8*, i32 } str, i32 n)
    """
    func_name = "string_sleft"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare external functions and intrinsics
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count_fn = declare_utf8_count_intrinsic(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    # Function signature: { i8*, i32 } string_sleft({ i8*, i32 } str, i32 n)
    fn_ty = ir.FunctionType(string_type, [string_type, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "n"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract data and size from input string
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Get total character count
    char_count = builder.call(utf8_count_fn, [data, size], name="char_count")

    # Clamp n to [0, char_count]
    zero = ir.Constant(i32, 0)
    n_clamped = builder.select(
        builder.icmp_signed("<", func.args[1], zero),
        zero,
        func.args[1],
        name="n_clamped"
    )
    n_final = builder.select(
        builder.icmp_signed(">", n_clamped, char_count),
        char_count,
        n_clamped,
        name="n_final"
    )

    # Find byte offset of the nth character
    byte_offset = builder.call(utf8_byte_offset_fn, [data, size, n_final], name="byte_offset")

    # If byte_offset is -1, use size (take entire string)
    byte_length = builder.select(
        builder.icmp_signed("<", byte_offset, zero),
        size,
        byte_offset,
        name="byte_length"
    )

    # Allocate and copy substring (from start of string)
    zero_offset = ir.Constant(i32, 0)
    result = allocate_substring(builder, malloc, memcpy, string_type, data, zero_offset, byte_length, i32, i64)
    builder.ret(result)
    return func


def emit_string_sright(module: ir.Module) -> ir.Function:
    """Emit the string.sright() method.

    Returns last n UTF-8 characters.
    UTF-8 aware - uses utf8_count and utf8_byte_offset.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_sright({ i8*, i32 } str, i32 n)
    """
    func_name = "string_sright"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare external functions and intrinsics
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count_fn = declare_utf8_count_intrinsic(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    # Function signature: { i8*, i32 } string_sright({ i8*, i32 } str, i32 n)
    fn_ty = ir.FunctionType(string_type, [string_type, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "n"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract data and size from input string
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Get total character count
    char_count = builder.call(utf8_count_fn, [data, size], name="char_count")

    # Clamp n to [0, char_count]
    zero = ir.Constant(i32, 0)
    n_clamped = builder.select(
        builder.icmp_signed("<", func.args[1], zero),
        zero,
        func.args[1],
        name="n_clamped"
    )
    n_final = builder.select(
        builder.icmp_signed(">", n_clamped, char_count),
        char_count,
        n_clamped,
        name="n_final"
    )

    # Calculate start character index: char_count - n_final
    start_char = builder.sub(char_count, n_final, name="start_char")

    # Find byte offset of start character
    start_byte = builder.call(utf8_byte_offset_fn, [data, size, start_char], name="start_byte")

    # If start_byte is -1, use 0 (shouldn't happen with clamping, but be safe)
    start_byte_final = builder.select(
        builder.icmp_signed("<", start_byte, zero),
        zero,
        start_byte,
        name="start_byte_final"
    )

    # Calculate byte length: size - start_byte_final
    byte_length = builder.sub(size, start_byte_final, name="byte_length")

    # Allocate and copy substring
    result = allocate_substring(builder, malloc, memcpy, string_type, data, start_byte_final, byte_length, i32, i64)
    builder.ret(result)
    return func


def emit_string_char_at(module: ir.Module) -> ir.Function:
    """Emit the string.char_at() method.

    Returns single UTF-8 character at the given character index.
    UTF-8 aware - uses utf8_byte_offset to find character boundaries.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_char_at({ i8*, i32 } str, i32 index)
    """
    func_name = "string_char_at"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare external functions and intrinsics
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    # Function signature: { i8*, i32 } string_char_at({ i8*, i32 } str, i32 index)
    fn_ty = ir.FunctionType(string_type, [string_type, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "index"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    valid_index_block = func.append_basic_block("valid_index")
    invalid_index_block = func.append_basic_block("invalid_index")
    merge_block = func.append_basic_block("merge")

    builder = ir.IRBuilder(entry_block)

    # Extract data and size from input string
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Find byte offset of character at index
    start_byte = builder.call(utf8_byte_offset_fn, [data, size, func.args[1]], name="start_byte")

    # Check if index is valid (start_byte >= 0)
    zero = ir.Constant(i32, 0)
    is_valid = builder.icmp_signed(">=", start_byte, zero, name="is_valid")
    builder.cbranch(is_valid, valid_index_block, invalid_index_block)

    # Valid index: find next character boundary
    builder.position_at_end(valid_index_block)
    next_index = builder.add(func.args[1], ir.Constant(i32, 1), name="next_index")
    end_byte = builder.call(utf8_byte_offset_fn, [data, size, next_index], name="end_byte")

    # If end_byte is -1, use size (last character)
    end_byte_final = builder.select(
        builder.icmp_signed("<", end_byte, zero),
        size,
        end_byte,
        name="end_byte_final"
    )

    # Calculate character byte length
    char_length = builder.sub(end_byte_final, start_byte, name="char_length")

    # Allocate and copy character substring
    result_valid = allocate_substring(builder, malloc, memcpy, string_type, data, start_byte, char_length, i32, i64)
    builder.branch(merge_block)

    # Invalid index: return empty string
    builder.position_at_end(invalid_index_block)
    empty_data = builder.call(malloc, [ir.Constant(i64, 1)], name="empty_data")
    result_invalid = build_string_struct(builder, string_type, empty_data, zero)
    builder.branch(merge_block)

    # Merge: phi node to select result
    builder.position_at_end(merge_block)
    result = builder.phi(string_type, name="result")
    result.add_incoming(result_valid, valid_index_block)
    result.add_incoming(result_invalid, invalid_index_block)
    builder.ret(result)

    return func


def emit_string_s(module: ir.Module) -> ir.Function:
    """Emit the string.s() method.

    Returns string slice from character index 'start' to character index 'end'.
    UTF-8 aware - uses utf8_byte_offset for character boundaries.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_s({ i8*, i32 } str, i32 start, i32 end)
    """
    func_name = "string_s"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare external functions and intrinsics
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count_fn = declare_utf8_count_intrinsic(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    # Function signature: { i8*, i32 } string_s({ i8*, i32 } str, i32 start, i32 end)
    fn_ty = ir.FunctionType(string_type, [string_type, i32, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "start"
    func.args[2].name = "end"

    # Create blocks
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract data and size from input string
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Get total character count
    char_count = builder.call(utf8_count_fn, [data, size], name="char_count")

    # Clamp start to [0, char_count]
    zero = ir.Constant(i32, 0)
    start_clamped = builder.select(
        builder.icmp_signed("<", func.args[1], zero),
        zero,
        func.args[1],
        name="start_clamped"
    )
    start_final = builder.select(
        builder.icmp_signed(">", start_clamped, char_count),
        char_count,
        start_clamped,
        name="start_final"
    )

    # Clamp end to [start_final, char_count]
    end_clamped = builder.select(
        builder.icmp_signed("<", func.args[2], start_final),
        start_final,
        func.args[2],
        name="end_clamped"
    )
    end_final = builder.select(
        builder.icmp_signed(">", end_clamped, char_count),
        char_count,
        end_clamped,
        name="end_final"
    )

    # Find byte offsets of start and end characters
    start_byte = builder.call(utf8_byte_offset_fn, [data, size, start_final], name="start_byte")
    end_byte = builder.call(utf8_byte_offset_fn, [data, size, end_final], name="end_byte")

    # If start_byte is -1, use 0
    start_byte_final = builder.select(
        builder.icmp_signed("<", start_byte, zero),
        zero,
        start_byte,
        name="start_byte_final"
    )

    # If end_byte is -1, use size
    end_byte_final = builder.select(
        builder.icmp_signed("<", end_byte, zero),
        size,
        end_byte,
        name="end_byte_final"
    )

    # Calculate byte length
    byte_length = builder.sub(end_byte_final, start_byte_final, name="byte_length")

    # Ensure byte_length is non-negative
    byte_length_final = builder.select(
        builder.icmp_signed("<", byte_length, zero),
        zero,
        byte_length,
        name="byte_length_final"
    )

    # Allocate and copy substring
    result = allocate_substring(builder, malloc, memcpy, string_type, data, start_byte_final, byte_length_final, i32, i64)
    builder.ret(result)
    return func
