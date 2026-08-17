"""String Slice Operations"""

import llvmlite.ir as ir
from ..intrinsics import declare_utf8_count_intrinsic, declare_utf8_byte_offset_intrinsic
from ..common import declare_malloc, declare_memcpy, allocate_substring, build_string_struct
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types


def emit_string_ss(module: ir.Module) -> ir.Function:
    """Emit `{i8*, i32} string_ss({i8*, i32} str, i32 start, i32 length)`."""
    func_name = "string_ss"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count_fn = declare_utf8_count_intrinsic(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type, i32, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "start"
    func.args[2].name = "length"

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    char_count = builder.call(utf8_count_fn, [data, size], name="char_count")

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

    remaining_chars = builder.sub(char_count, start_final, name="remaining_chars")

    length_clamped = builder.select(
        builder.icmp_signed("<", func.args[2], zero),
        zero,
        func.args[2],
        name="length_clamped"
    )
    length_final = builder.select(
        builder.icmp_signed(">", length_clamped, remaining_chars),
        remaining_chars,
        length_clamped,
        name="length_final"
    )

    end_char = builder.add(start_final, length_final, name="end_char")

    start_byte = builder.call(utf8_byte_offset_fn, [data, size, start_final], name="start_byte")
    end_byte = builder.call(utf8_byte_offset_fn, [data, size, end_char], name="end_byte")

    start_byte_final = builder.select(
        builder.icmp_signed("<", start_byte, zero),
        zero,
        start_byte,
        name="start_byte_final"
    )

    end_byte_final = builder.select(
        builder.icmp_signed("<", end_byte, zero),
        size,
        end_byte,
        name="end_byte_final"
    )

    byte_length = builder.sub(end_byte_final, start_byte_final, name="byte_length")

    byte_length_final = builder.select(
        builder.icmp_signed("<", byte_length, zero),
        zero,
        byte_length,
        name="byte_length_final"
    )

    result = allocate_substring(builder, malloc, memcpy, string_type, data, start_byte_final, byte_length_final, i32, i64)
    builder.ret(result)
    return func


def emit_string_sleft(module: ir.Module) -> ir.Function:
    """Emit `{i8*, i32} string_sleft({i8*, i32} str, i32 n)`."""
    func_name = "string_sleft"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count_fn = declare_utf8_count_intrinsic(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "n"

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    char_count = builder.call(utf8_count_fn, [data, size], name="char_count")

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

    byte_offset = builder.call(utf8_byte_offset_fn, [data, size, n_final], name="byte_offset")

    byte_length = builder.select(
        builder.icmp_signed("<", byte_offset, zero),
        size,
        byte_offset,
        name="byte_length"
    )

    zero_offset = ir.Constant(i32, 0)
    result = allocate_substring(builder, malloc, memcpy, string_type, data, zero_offset, byte_length, i32, i64)
    builder.ret(result)
    return func


def emit_string_sright(module: ir.Module) -> ir.Function:
    """Emit `{i8*, i32} string_sright({i8*, i32} str, i32 n)`."""
    func_name = "string_sright"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count_fn = declare_utf8_count_intrinsic(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "n"

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    char_count = builder.call(utf8_count_fn, [data, size], name="char_count")

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

    start_char = builder.sub(char_count, n_final, name="start_char")

    start_byte = builder.call(utf8_byte_offset_fn, [data, size, start_char], name="start_byte")

    start_byte_final = builder.select(
        builder.icmp_signed("<", start_byte, zero),
        zero,
        start_byte,
        name="start_byte_final"
    )

    byte_length = builder.sub(size, start_byte_final, name="byte_length")

    result = allocate_substring(builder, malloc, memcpy, string_type, data, start_byte_final, byte_length, i32, i64)
    builder.ret(result)
    return func


def emit_string_char_at(module: ir.Module) -> ir.Function:
    """Emit `{i8*, i32} string_char_at({i8*, i32} str, i32 index)`."""
    func_name = "string_char_at"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "index"

    entry_block = func.append_basic_block("entry")
    valid_index_block = func.append_basic_block("valid_index")
    invalid_index_block = func.append_basic_block("invalid_index")
    merge_block = func.append_basic_block("merge")

    builder = ir.IRBuilder(entry_block)

    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    start_byte = builder.call(utf8_byte_offset_fn, [data, size, func.args[1]], name="start_byte")

    zero = ir.Constant(i32, 0)
    is_valid = builder.icmp_signed(">=", start_byte, zero, name="is_valid")
    builder.cbranch(is_valid, valid_index_block, invalid_index_block)

    builder.position_at_end(valid_index_block)
    next_index = builder.add(func.args[1], ir.Constant(i32, 1), name="next_index")
    end_byte = builder.call(utf8_byte_offset_fn, [data, size, next_index], name="end_byte")

    end_byte_final = builder.select(
        builder.icmp_signed("<", end_byte, zero),
        size,
        end_byte,
        name="end_byte_final"
    )

    char_length = builder.sub(end_byte_final, start_byte, name="char_length")

    result_valid = allocate_substring(builder, malloc, memcpy, string_type, data, start_byte, char_length, i32, i64)
    builder.branch(merge_block)

    builder.position_at_end(invalid_index_block)
    empty_data = builder.call(malloc, [ir.Constant(i64, 1)], name="empty_data")
    result_invalid = build_string_struct(builder, string_type, empty_data, zero, owned=1)
    builder.branch(merge_block)

    builder.position_at_end(merge_block)
    result = builder.phi(string_type, name="result")
    result.add_incoming(result_valid, valid_index_block)
    result.add_incoming(result_invalid, invalid_index_block)
    builder.ret(result)

    return func


def emit_string_s(module: ir.Module) -> ir.Function:
    """Emit `{i8*, i32} string_s({i8*, i32} str, i32 start, i32 end)`."""
    func_name = "string_s"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count_fn = declare_utf8_count_intrinsic(module)
    utf8_byte_offset_fn = declare_utf8_byte_offset_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type, i32, i32])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "start"
    func.args[2].name = "end"

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    char_count = builder.call(utf8_count_fn, [data, size], name="char_count")

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

    start_byte = builder.call(utf8_byte_offset_fn, [data, size, start_final], name="start_byte")
    end_byte = builder.call(utf8_byte_offset_fn, [data, size, end_final], name="end_byte")

    start_byte_final = builder.select(
        builder.icmp_signed("<", start_byte, zero),
        zero,
        start_byte,
        name="start_byte_final"
    )

    end_byte_final = builder.select(
        builder.icmp_signed("<", end_byte, zero),
        size,
        end_byte,
        name="end_byte_final"
    )

    byte_length = builder.sub(end_byte_final, start_byte_final, name="byte_length")

    byte_length_final = builder.select(
        builder.icmp_signed("<", byte_length, zero),
        zero,
        byte_length,
        name="byte_length_final"
    )

    result = allocate_substring(builder, malloc, memcpy, string_type, data, start_byte_final, byte_length_final, i32, i64)
    builder.ret(result)
    return func
