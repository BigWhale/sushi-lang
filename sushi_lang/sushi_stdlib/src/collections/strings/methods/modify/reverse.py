"""String Reverse Operations"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc, declare_memcpy
from ...intrinsics import declare_utf8_count_intrinsic, declare_utf8_byte_offset_intrinsic
from ...common import build_string_struct, clone_string_to_owned


def emit_string_reverse(module: ir.Module) -> ir.Function:
    """Emit `{i8*, i32} string_reverse({i8*, i32} str)`."""
    func_name = "string_reverse"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    utf8_count = declare_utf8_count_intrinsic(module)
    utf8_byte_offset = declare_utf8_byte_offset_intrinsic(module)

    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    entry_block = func.append_basic_block("entry")
    empty_check = func.append_basic_block("empty_check")
    single_byte_check = func.append_basic_block("single_byte_check")
    reverse_block = func.append_basic_block("reverse")
    loop_cond = func.append_basic_block("loop_cond")
    loop_body = func.append_basic_block("loop_body")
    loop_done = func.append_basic_block("loop_done")
    return_original = func.append_basic_block("return_original")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    builder.branch(empty_check)

    builder = ir.IRBuilder(empty_check)
    is_empty = builder.icmp_unsigned("==", str_size, ir.Constant(i32, 0), name="is_empty")
    builder.cbranch(is_empty, return_original, single_byte_check)

    builder = ir.IRBuilder(single_byte_check)
    is_single = builder.icmp_unsigned("==", str_size, ir.Constant(i32, 1), name="is_single")
    builder.cbranch(is_single, return_original, reverse_block)

    builder = ir.IRBuilder(reverse_block)
    char_count = builder.call(utf8_count, [str_data, str_size], name="char_count")

    str_size_i64 = builder.zext(str_size, i64, name="str_size_i64")
    result_data = builder.call(malloc, [str_size_i64], name="result_data")

    initial_char_index = builder.sub(char_count, ir.Constant(i32, 1), name="initial_char_index")

    builder.branch(loop_cond)

    builder = ir.IRBuilder(loop_cond)
    char_index_phi = builder.phi(i32, name="char_index")
    output_pos_phi = builder.phi(i32, name="output_pos")

    char_index_phi.add_incoming(initial_char_index, reverse_block)
    output_pos_phi.add_incoming(ir.Constant(i32, 0), reverse_block)

    continue_loop = builder.icmp_signed(">=", char_index_phi, ir.Constant(i32, 0), name="continue_loop")
    builder.cbranch(continue_loop, loop_body, loop_done)

    builder = ir.IRBuilder(loop_body)

    current_byte_offset = builder.call(
        utf8_byte_offset,
        [str_data, str_size, char_index_phi],
        name="current_byte_offset"
    )

    next_char_index = builder.add(char_index_phi, ir.Constant(i32, 1), name="next_char_index")
    next_byte_offset = builder.call(
        utf8_byte_offset,
        [str_data, str_size, next_char_index],
        name="next_byte_offset"
    )

    char_byte_length = builder.sub(next_byte_offset, current_byte_offset, name="char_byte_length")

    src_ptr = builder.gep(str_data, [current_byte_offset], name="src_ptr")
    dst_ptr = builder.gep(result_data, [output_pos_phi], name="dst_ptr")

    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [dst_ptr, src_ptr, builder.zext(char_byte_length, ir.IntType(64)), is_volatile])

    char_index_next = builder.sub(char_index_phi, ir.Constant(i32, 1), name="char_index_next")
    output_pos_next = builder.add(output_pos_phi, char_byte_length, name="output_pos_next")

    char_index_phi.add_incoming(char_index_next, loop_body)
    output_pos_phi.add_incoming(output_pos_next, loop_body)

    builder.branch(loop_cond)

    builder = ir.IRBuilder(loop_done)
    result_string = build_string_struct(builder, string_type, result_data, str_size, owned=1)
    builder.ret(result_string)

    # Return original: reversal is identity here, but clone so the result is independently
    # owned (aliasing the input's buffer would double-free under string RAII, issue #145).
    builder = ir.IRBuilder(return_original)
    builder.ret(clone_string_to_owned(builder, module, func.args[0], string_type))

    return func
