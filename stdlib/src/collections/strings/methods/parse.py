"""
String Parsing Operations

Implements parsing methods that convert strings to numeric types:
- to_i32(): Parse string to Maybe<i32>
- to_i64(): Parse string to Maybe<i64>
- to_f64(): Parse string to Maybe<f64>

All methods return Maybe<T> to handle parse failures gracefully:
- Maybe.Some(value) on successful parse
- Maybe.None() on failure (invalid format, overflow, etc.)

Uses C standard library functions (strtol, strtoll, strtod) for robust parsing.
"""

import llvmlite.ir as ir
from stdlib.src.libc_declarations import declare_strtol, declare_strtoll, declare_strtod, declare_malloc
from stdlib.src.type_definitions import get_string_types


def emit_string_to_i32(module: ir.Module) -> ir.Function:
    """Emit the string.to_i32() method.

    Parses a string to Maybe<i32> using C strtol() function.

    Algorithm:
    1. Null-terminate the string (strtol requires null-terminated C string)
    2. Call strtol(str, &endptr, 10) to parse as base-10 integer
    3. Check if parse was successful (endptr != str && *endptr == '\\0')
    4. Check if value fits in i32 range
    5. Return Maybe.Some(i32_value) on success, Maybe.None() on failure

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: Maybe<i32> string_to_i32({ i8*, i32 } str)
    """
    func_name = "string_to_i32"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()
    i1 = ir.IntType(1)

    # Maybe<i32> = {i32 tag, [4 x i8] data}
    # tag = 0 for Some(i32), 1 for None()
    # data holds the i32 value when tag=0
    maybe_i32_type = ir.LiteralStructType([i32, ir.ArrayType(i8, 4)])

    # Declare external functions
    malloc = declare_malloc(module)
    strtol = declare_strtol(module)

    # Function signature: Maybe<i32> string_to_i32({ i8*, i32 } str)
    fn_ty = ir.FunctionType(maybe_i32_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create basic blocks
    entry_block = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    failure_block = func.append_basic_block("failure")
    return_block = func.append_basic_block("return")

    # Entry block: Null-terminate string and call strtol
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")

    # Allocate buffer for null-terminated string (size + 1)
    size_plus_one = builder.add(str_size, ir.Constant(i32, 1), name="size_plus_one")
    size_plus_one_i64 = builder.zext(size_plus_one, i64, name="size_plus_one_i64")
    buffer = builder.call(malloc, [size_plus_one_i64], name="buffer")

    # Copy string data to buffer
    # Use simple byte-by-byte copy (could use memcpy but this is clearer)
    idx_ptr = builder.alloca(i32, name="idx_ptr")
    builder.store(ir.Constant(i32, 0), idx_ptr)

    copy_loop = func.append_basic_block("copy_loop")
    copy_body = func.append_basic_block("copy_body")
    copy_done = func.append_basic_block("copy_done")

    builder.branch(copy_loop)

    # Copy loop
    builder.position_at_end(copy_loop)
    idx = builder.load(idx_ptr, name="idx")
    cond = builder.icmp_signed("<", idx, str_size, name="cond")
    builder.cbranch(cond, copy_body, copy_done)

    # Copy body
    builder.position_at_end(copy_body)
    src_ptr = builder.gep(str_data, [idx], name="src_ptr")
    dst_ptr = builder.gep(buffer, [idx], name="dst_ptr")
    byte = builder.load(src_ptr, name="byte")
    builder.store(byte, dst_ptr)
    next_idx = builder.add(idx, ir.Constant(i32, 1), name="next_idx")
    builder.store(next_idx, idx_ptr)
    builder.branch(copy_loop)

    # Copy done: add null terminator
    builder.position_at_end(copy_done)
    null_ptr = builder.gep(buffer, [str_size], name="null_ptr")
    builder.store(ir.Constant(i8, 0), null_ptr)

    # Call strtol(buffer, &endptr, 10)
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

    # Check i32 range: INT32_MIN (-2^31) to INT32_MAX (2^31-1)
    i32_min = ir.Constant(i64, -2147483648)
    i32_max = ir.Constant(i64, 2147483647)
    in_range_low = builder.icmp_signed(">=", result_i64, i32_min, name="in_range_low")
    in_range_high = builder.icmp_signed("<=", result_i64, i32_max, name="in_range_high")
    in_range = builder.and_(in_range_low, in_range_high, name="in_range")

    success = builder.and_(parse_ok, in_range, name="success")
    builder.cbranch(success, success_block, failure_block)

    # Success block: return Maybe.Some(i32_value)
    builder.position_at_end(success_block)
    result_i32 = builder.trunc(result_i64, i32, name="result_i32")

    # Build Maybe.Some variant: {i32 tag=0, [4 x i8] data=packed_i32}
    undef_some = ir.Constant(maybe_i32_type, ir.Undefined)
    some_with_tag = builder.insert_value(undef_some, ir.Constant(i32, 0), 0, name="some_with_tag")

    # Pack i32 into [4 x i8] array
    data_temp = builder.alloca(ir.ArrayType(i8, 4), name="data_temp")
    data_temp_i8 = builder.bitcast(data_temp, i8_ptr, name="data_temp_i8")
    data_temp_i32 = builder.bitcast(data_temp_i8, i32.as_pointer(), name="data_temp_i32")
    builder.store(result_i32, data_temp_i32)
    packed_data = builder.load(data_temp, name="packed_data")

    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some_complete")
    builder.branch(return_block)

    # Failure block: return Maybe.None()
    builder.position_at_end(failure_block)
    undef_none = ir.Constant(maybe_i32_type, ir.Undefined)
    none_with_tag = builder.insert_value(undef_none, ir.Constant(i32, 1), 0, name="none_with_tag")
    # data field is undefined for None
    undef_data = ir.Constant(ir.ArrayType(i8, 4), ir.Undefined)
    none_complete = builder.insert_value(none_with_tag, undef_data, 1, name="none_complete")
    builder.branch(return_block)

    # Return block: phi node merges both paths
    builder.position_at_end(return_block)
    result_phi = builder.phi(maybe_i32_type, name="result")
    result_phi.add_incoming(some_complete, success_block)
    result_phi.add_incoming(none_complete, failure_block)
    builder.ret(result_phi)

    return func


def emit_string_to_i64(module: ir.Module) -> ir.Function:
    """Emit the string.to_i64() method.

    Parses a string to Maybe<i64> using C strtoll() function.

    Algorithm: Similar to to_i32() but uses strtoll and returns i64.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: Maybe<i64> string_to_i64({ i8*, i32 } str)
    """
    func_name = "string_to_i64"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Maybe<i64> = {i32 tag, [8 x i8] data}
    maybe_i64_type = ir.LiteralStructType([i32, ir.ArrayType(i8, 8)])

    # Declare external functions
    malloc = declare_malloc(module)
    strtoll = declare_strtoll(module)

    # Function signature: Maybe<i64> string_to_i64({ i8*, i32 } str)
    fn_ty = ir.FunctionType(maybe_i64_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create basic blocks
    entry_block = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    failure_block = func.append_basic_block("failure")
    return_block = func.append_basic_block("return")

    # Entry block: Null-terminate string and call strtoll
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")

    # Allocate buffer for null-terminated string
    size_plus_one = builder.add(str_size, ir.Constant(i32, 1), name="size_plus_one")
    size_plus_one_i64 = builder.zext(size_plus_one, i64, name="size_plus_one_i64")
    buffer = builder.call(malloc, [size_plus_one_i64], name="buffer")

    # Copy string data
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

    # Call strtoll(buffer, &endptr, 10)
    endptr_storage = builder.alloca(i8_ptr, name="endptr_storage")
    base = ir.Constant(i32, 10)
    result_i64 = builder.call(strtoll, [buffer, endptr_storage, base], name="result_i64")
    endptr = builder.load(endptr_storage, name="endptr")

    # Check if parsing succeeded
    endptr_not_buffer = builder.icmp_unsigned("!=", endptr, buffer, name="endptr_not_buffer")
    endptr_char = builder.load(endptr, name="endptr_char")
    endptr_is_null = builder.icmp_signed("==", endptr_char, ir.Constant(i8, 0), name="endptr_is_null")
    success = builder.and_(endptr_not_buffer, endptr_is_null, name="success")

    builder.cbranch(success, success_block, failure_block)

    # Success block: return Maybe.Some(i64_value)
    builder.position_at_end(success_block)

    # Build Maybe.Some variant
    undef_some = ir.Constant(maybe_i64_type, ir.Undefined)
    some_with_tag = builder.insert_value(undef_some, ir.Constant(i32, 0), 0, name="some_with_tag")

    # Pack i64 into [8 x i8] array
    data_temp = builder.alloca(ir.ArrayType(i8, 8), name="data_temp")
    data_temp_i8 = builder.bitcast(data_temp, i8_ptr, name="data_temp_i8")
    data_temp_i64 = builder.bitcast(data_temp_i8, i64.as_pointer(), name="data_temp_i64")
    builder.store(result_i64, data_temp_i64)
    packed_data = builder.load(data_temp, name="packed_data")

    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some_complete")
    builder.branch(return_block)

    # Failure block: return Maybe.None()
    builder.position_at_end(failure_block)
    undef_none = ir.Constant(maybe_i64_type, ir.Undefined)
    none_with_tag = builder.insert_value(undef_none, ir.Constant(i32, 1), 0, name="none_with_tag")
    undef_data = ir.Constant(ir.ArrayType(i8, 8), ir.Undefined)
    none_complete = builder.insert_value(none_with_tag, undef_data, 1, name="none_complete")
    builder.branch(return_block)

    # Return block
    builder.position_at_end(return_block)
    result_phi = builder.phi(maybe_i64_type, name="result")
    result_phi.add_incoming(some_complete, success_block)
    result_phi.add_incoming(none_complete, failure_block)
    builder.ret(result_phi)

    return func


def emit_string_to_f64(module: ir.Module) -> ir.Function:
    """Emit the string.to_f64() method.

    Parses a string to Maybe<f64> using C strtod() function.

    Algorithm: Similar to to_i32() but uses strtod and returns f64.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: Maybe<f64> string_to_f64({ i8*, i32 } str)
    """
    func_name = "string_to_f64"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()
    f64 = ir.DoubleType()

    # Maybe<f64> = {i32 tag, [8 x i8] data}
    maybe_f64_type = ir.LiteralStructType([i32, ir.ArrayType(i8, 8)])

    # Declare external functions
    malloc = declare_malloc(module)
    strtod = declare_strtod(module)

    # Function signature: Maybe<f64> string_to_f64({ i8*, i32 } str)
    fn_ty = ir.FunctionType(maybe_f64_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create basic blocks
    entry_block = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    failure_block = func.append_basic_block("failure")
    return_block = func.append_basic_block("return")

    # Entry block: Null-terminate string and call strtod
    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")

    # Allocate buffer for null-terminated string
    size_plus_one = builder.add(str_size, ir.Constant(i32, 1), name="size_plus_one")
    size_plus_one_i64 = builder.zext(size_plus_one, i64, name="size_plus_one_i64")
    buffer = builder.call(malloc, [size_plus_one_i64], name="buffer")

    # Copy string data
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

    # Call strtod(buffer, &endptr)
    endptr_storage = builder.alloca(i8_ptr, name="endptr_storage")
    result_f64 = builder.call(strtod, [buffer, endptr_storage], name="result_f64")
    endptr = builder.load(endptr_storage, name="endptr")

    # Check if parsing succeeded
    endptr_not_buffer = builder.icmp_unsigned("!=", endptr, buffer, name="endptr_not_buffer")
    endptr_char = builder.load(endptr, name="endptr_char")
    endptr_is_null = builder.icmp_signed("==", endptr_char, ir.Constant(i8, 0), name="endptr_is_null")
    success = builder.and_(endptr_not_buffer, endptr_is_null, name="success")

    builder.cbranch(success, success_block, failure_block)

    # Success block: return Maybe.Some(f64_value)
    builder.position_at_end(success_block)

    # Build Maybe.Some variant
    undef_some = ir.Constant(maybe_f64_type, ir.Undefined)
    some_with_tag = builder.insert_value(undef_some, ir.Constant(i32, 0), 0, name="some_with_tag")

    # Pack f64 into [8 x i8] array
    data_temp = builder.alloca(ir.ArrayType(i8, 8), name="data_temp")
    data_temp_i8 = builder.bitcast(data_temp, i8_ptr, name="data_temp_i8")
    data_temp_f64 = builder.bitcast(data_temp_i8, f64.as_pointer(), name="data_temp_f64")
    builder.store(result_f64, data_temp_f64)
    packed_data = builder.load(data_temp, name="packed_data")

    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some_complete")
    builder.branch(return_block)

    # Failure block: return Maybe.None()
    builder.position_at_end(failure_block)
    undef_none = ir.Constant(maybe_f64_type, ir.Undefined)
    none_with_tag = builder.insert_value(undef_none, ir.Constant(i32, 1), 0, name="none_with_tag")
    undef_data = ir.Constant(ir.ArrayType(i8, 8), ir.Undefined)
    none_complete = builder.insert_value(none_with_tag, undef_data, 1, name="none_complete")
    builder.branch(return_block)

    # Return block
    builder.position_at_end(return_block)
    result_phi = builder.phi(maybe_f64_type, name="result")
    result_phi.add_incoming(some_complete, success_block)
    result_phi.add_incoming(none_complete, failure_block)
    builder.ret(result_phi)

    return func
