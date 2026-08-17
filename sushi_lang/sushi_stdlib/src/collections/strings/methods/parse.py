"""String Parsing Operations"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_strtol, declare_strtoll, declare_strtod, declare_malloc
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types, get_maybe_type


def emit_string_to_i32(module: ir.Module) -> ir.Function:
    """Emit `Maybe<i32> string_to_i32({i8*, i32} str)`."""
    func_name = "string_to_i32"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Maybe<i32> = {i32 tag, [1 x i64] data} (#300 phase 2)
    # tag = 0 for Some(i32), 1 for None()
    # data holds the i32 value when tag=0 (payload offset 0)
    maybe_i32_type = get_maybe_type(i32)

    malloc = declare_malloc(module)
    strtol = declare_strtol(module)

    fn_ty = ir.FunctionType(maybe_i32_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    entry_block = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    failure_block = func.append_basic_block("failure")
    return_block = func.append_basic_block("return")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")

    size_plus_one = builder.add(str_size, ir.Constant(i32, 1), name="size_plus_one")
    size_plus_one_i64 = builder.zext(size_plus_one, i64, name="size_plus_one_i64")
    buffer = builder.call(malloc, [size_plus_one_i64], name="buffer")

    idx_ptr = builder.alloca(i32, name="idx_ptr")
    builder.store(ir.Constant(i32, 0), idx_ptr)

    copy_loop = func.append_basic_block("copy_loop")
    copy_body = func.append_basic_block("copy_body")
    copy_done = func.append_basic_block("copy_done")

    builder.branch(copy_loop)

    builder.position_at_end(copy_loop)
    idx = builder.load(idx_ptr, name="idx")
    cond = builder.icmp_signed("<", idx, str_size, name="cond")
    builder.cbranch(cond, copy_body, copy_done)

    builder.position_at_end(copy_body)
    src_ptr = builder.gep(str_data, [idx], name="src_ptr")
    dst_ptr = builder.gep(buffer, [idx], name="dst_ptr")
    byte = builder.load(src_ptr, name="byte")
    builder.store(byte, dst_ptr)
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx_ptr)
    builder.branch(copy_loop)

    builder.position_at_end(copy_done)
    null_ptr = builder.gep(buffer, [str_size], name="null_ptr")
    builder.store(ir.Constant(i8, 0), null_ptr)

    endptr_storage = builder.alloca(i8_ptr, name="endptr_storage")
    base = ir.Constant(i32, 10)
    result_i64 = builder.call(strtol, [buffer, endptr_storage, base], name="result_i64")
    endptr = builder.load(endptr_storage, name="endptr")

    # Check if parsing succeeded:
    # 1. endptr != buffer (some characters were consumed)
    # 2. *endptr == '\0' (entire string was consumed)
    # 3. result fits in i32 range (-2147483648 to 2147483647)
    endptr_not_buffer = builder.icmp_unsigned("!=", endptr, buffer, name="endptr_not_buffer")
    endptr_char = builder.load(endptr, name="endptr_char")
    endptr_is_null = builder.icmp_signed("==", endptr_char, ir.Constant(i8, 0), name="endptr_is_null")
    parse_ok = builder.and_(endptr_not_buffer, endptr_is_null, name="parse_ok")

    i32_min = ir.Constant(i64, -2147483648)
    i32_max = ir.Constant(i64, 2147483647)
    in_range_low = builder.icmp_signed(">=", result_i64, i32_min, name="in_range_low")
    in_range_high = builder.icmp_signed("<=", result_i64, i32_max, name="in_range_high")
    in_range = builder.and_(in_range_low, in_range_high, name="in_range")

    success = builder.and_(parse_ok, in_range, name="success")
    builder.cbranch(success, success_block, failure_block)

    builder.position_at_end(success_block)
    result_i32 = builder.trunc(result_i64, i32, name="result_i32")

    undef_some = ir.Constant(maybe_i32_type, ir.Undefined)
    some_with_tag = builder.insert_value(undef_some, ir.Constant(i32, 0), 0, name="some_with_tag")

    data_temp = builder.alloca(maybe_i32_type.elements[1], name="data_temp")
    data_temp_i8 = builder.bitcast(data_temp, i8_ptr, name="data_temp_i8")
    data_temp_i32 = builder.bitcast(data_temp_i8, i32.as_pointer(), name="data_temp_i32")
    builder.store(result_i32, data_temp_i32)
    packed_data = builder.load(data_temp, name="packed_data")

    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some_complete")
    builder.branch(return_block)

    builder.position_at_end(failure_block)
    undef_none = ir.Constant(maybe_i32_type, ir.Undefined)
    none_with_tag = builder.insert_value(undef_none, ir.Constant(i32, 1), 0, name="none_with_tag")
    undef_data = ir.Constant(maybe_i32_type.elements[1], ir.Undefined)
    none_complete = builder.insert_value(none_with_tag, undef_data, 1, name="none_complete")
    builder.branch(return_block)

    builder.position_at_end(return_block)
    result_phi = builder.phi(maybe_i32_type, name="result")
    result_phi.add_incoming(some_complete, success_block)
    result_phi.add_incoming(none_complete, failure_block)
    builder.ret(result_phi)

    return func


def emit_string_to_i64(module: ir.Module) -> ir.Function:
    """Emit `Maybe<i64> string_to_i64({i8*, i32} str)`."""
    func_name = "string_to_i64"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Maybe<i64> = {i32 tag, [1 x i64] data} (#300 phase 2)
    maybe_i64_type = get_maybe_type(i64)

    malloc = declare_malloc(module)
    strtoll = declare_strtoll(module)

    fn_ty = ir.FunctionType(maybe_i64_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    entry_block = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    failure_block = func.append_basic_block("failure")
    return_block = func.append_basic_block("return")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")

    size_plus_one = builder.add(str_size, ir.Constant(i32, 1), name="size_plus_one")
    size_plus_one_i64 = builder.zext(size_plus_one, i64, name="size_plus_one_i64")
    buffer = builder.call(malloc, [size_plus_one_i64], name="buffer")

    idx_ptr = builder.alloca(i32, name="idx_ptr")
    builder.store(ir.Constant(i32, 0), idx_ptr)

    copy_loop = func.append_basic_block("copy_loop")
    copy_body = func.append_basic_block("copy_body")
    copy_done = func.append_basic_block("copy_done")

    builder.branch(copy_loop)

    builder.position_at_end(copy_loop)
    idx = builder.load(idx_ptr, name="idx")
    cond = builder.icmp_signed("<", idx, str_size, name="cond")
    builder.cbranch(cond, copy_body, copy_done)

    builder.position_at_end(copy_body)
    src_ptr = builder.gep(str_data, [idx], name="src_ptr")
    dst_ptr = builder.gep(buffer, [idx], name="dst_ptr")
    byte = builder.load(src_ptr, name="byte")
    builder.store(byte, dst_ptr)
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx_ptr)
    builder.branch(copy_loop)

    builder.position_at_end(copy_done)
    null_ptr = builder.gep(buffer, [str_size], name="null_ptr")
    builder.store(ir.Constant(i8, 0), null_ptr)

    endptr_storage = builder.alloca(i8_ptr, name="endptr_storage")
    base = ir.Constant(i32, 10)
    result_i64 = builder.call(strtoll, [buffer, endptr_storage, base], name="result_i64")
    endptr = builder.load(endptr_storage, name="endptr")

    endptr_not_buffer = builder.icmp_unsigned("!=", endptr, buffer, name="endptr_not_buffer")
    endptr_char = builder.load(endptr, name="endptr_char")
    endptr_is_null = builder.icmp_signed("==", endptr_char, ir.Constant(i8, 0), name="endptr_is_null")
    success = builder.and_(endptr_not_buffer, endptr_is_null, name="success")

    builder.cbranch(success, success_block, failure_block)

    builder.position_at_end(success_block)

    undef_some = ir.Constant(maybe_i64_type, ir.Undefined)
    some_with_tag = builder.insert_value(undef_some, ir.Constant(i32, 0), 0, name="some_with_tag")

    data_temp = builder.alloca(maybe_i64_type.elements[1], name="data_temp")
    data_temp_i8 = builder.bitcast(data_temp, i8_ptr, name="data_temp_i8")
    data_temp_i64 = builder.bitcast(data_temp_i8, i64.as_pointer(), name="data_temp_i64")
    builder.store(result_i64, data_temp_i64)
    packed_data = builder.load(data_temp, name="packed_data")

    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some_complete")
    builder.branch(return_block)

    builder.position_at_end(failure_block)
    undef_none = ir.Constant(maybe_i64_type, ir.Undefined)
    none_with_tag = builder.insert_value(undef_none, ir.Constant(i32, 1), 0, name="none_with_tag")
    undef_data = ir.Constant(maybe_i64_type.elements[1], ir.Undefined)
    none_complete = builder.insert_value(none_with_tag, undef_data, 1, name="none_complete")
    builder.branch(return_block)

    builder.position_at_end(return_block)
    result_phi = builder.phi(maybe_i64_type, name="result")
    result_phi.add_incoming(some_complete, success_block)
    result_phi.add_incoming(none_complete, failure_block)
    builder.ret(result_phi)

    return func


def emit_string_to_f64(module: ir.Module) -> ir.Function:
    """Emit `Maybe<f64> string_to_f64({i8*, i32} str)`."""
    func_name = "string_to_f64"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()
    f64 = ir.DoubleType()

    # Maybe<f64> = {i32 tag, [1 x i64] data} (#300 phase 2)
    maybe_f64_type = get_maybe_type(f64)

    malloc = declare_malloc(module)
    strtod = declare_strtod(module)

    fn_ty = ir.FunctionType(maybe_f64_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    entry_block = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    failure_block = func.append_basic_block("failure")
    return_block = func.append_basic_block("return")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")

    size_plus_one = builder.add(str_size, ir.Constant(i32, 1), name="size_plus_one")
    size_plus_one_i64 = builder.zext(size_plus_one, i64, name="size_plus_one_i64")
    buffer = builder.call(malloc, [size_plus_one_i64], name="buffer")

    idx_ptr = builder.alloca(i32, name="idx_ptr")
    builder.store(ir.Constant(i32, 0), idx_ptr)

    copy_loop = func.append_basic_block("copy_loop")
    copy_body = func.append_basic_block("copy_body")
    copy_done = func.append_basic_block("copy_done")

    builder.branch(copy_loop)

    builder.position_at_end(copy_loop)
    idx = builder.load(idx_ptr, name="idx")
    cond = builder.icmp_signed("<", idx, str_size, name="cond")
    builder.cbranch(cond, copy_body, copy_done)

    builder.position_at_end(copy_body)
    src_ptr = builder.gep(str_data, [idx], name="src_ptr")
    dst_ptr = builder.gep(buffer, [idx], name="dst_ptr")
    byte = builder.load(src_ptr, name="byte")
    builder.store(byte, dst_ptr)
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx_ptr)
    builder.branch(copy_loop)

    builder.position_at_end(copy_done)
    null_ptr = builder.gep(buffer, [str_size], name="null_ptr")
    builder.store(ir.Constant(i8, 0), null_ptr)

    endptr_storage = builder.alloca(i8_ptr, name="endptr_storage")
    result_f64 = builder.call(strtod, [buffer, endptr_storage], name="result_f64")
    endptr = builder.load(endptr_storage, name="endptr")

    endptr_not_buffer = builder.icmp_unsigned("!=", endptr, buffer, name="endptr_not_buffer")
    endptr_char = builder.load(endptr, name="endptr_char")
    endptr_is_null = builder.icmp_signed("==", endptr_char, ir.Constant(i8, 0), name="endptr_is_null")
    success = builder.and_(endptr_not_buffer, endptr_is_null, name="success")

    builder.cbranch(success, success_block, failure_block)

    builder.position_at_end(success_block)

    undef_some = ir.Constant(maybe_f64_type, ir.Undefined)
    some_with_tag = builder.insert_value(undef_some, ir.Constant(i32, 0), 0, name="some_with_tag")

    data_temp = builder.alloca(maybe_f64_type.elements[1], name="data_temp")
    data_temp_i8 = builder.bitcast(data_temp, i8_ptr, name="data_temp_i8")
    data_temp_f64 = builder.bitcast(data_temp_i8, f64.as_pointer(), name="data_temp_f64")
    builder.store(result_f64, data_temp_f64)
    packed_data = builder.load(data_temp, name="packed_data")

    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some_complete")
    builder.branch(return_block)

    builder.position_at_end(failure_block)
    undef_none = ir.Constant(maybe_f64_type, ir.Undefined)
    none_with_tag = builder.insert_value(undef_none, ir.Constant(i32, 1), 0, name="none_with_tag")
    undef_data = ir.Constant(maybe_f64_type.elements[1], ir.Undefined)
    none_complete = builder.insert_value(none_with_tag, undef_data, 1, name="none_complete")
    builder.branch(return_block)

    builder.position_at_end(return_block)
    result_phi = builder.phi(maybe_f64_type, name="result")
    result_phi.add_incoming(some_complete, success_block)
    result_phi.add_incoming(none_complete, failure_block)
    builder.ret(result_phi)

    return func
