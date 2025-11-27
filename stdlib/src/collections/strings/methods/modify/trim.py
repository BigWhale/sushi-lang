"""
String Strip/Trim Operations

Implements the strip_prefix() and strip_suffix() methods for fat pointer strings.
"""

import llvmlite.ir as ir
from stdlib.src.type_definitions import get_string_types
from stdlib.src.libc_declarations import declare_malloc, declare_memcpy
from ...common import build_string_struct


def emit_string_strip_prefix(module: ir.Module) -> ir.Function:
    """Emit the string.strip_prefix() method.

    Removes prefix if present at the beginning of the string.
    If the string doesn't start with the prefix, returns the original string.

    Algorithm:
    1. Check if string starts with prefix (compare bytes)
    2. If matches: return substring from prefix.size to end
    3. If no match: return original string unchanged

    Examples:
        "hello".strip_prefix("he") -> "llo"
        "hello".strip_prefix("x") -> "hello"
        "test".strip_prefix("testing") -> "test"
        "".strip_prefix("x") -> ""

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_strip_prefix({ i8*, i32 } str, { i8*, i32 } prefix)
    """
    func_name = "string_strip_prefix"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    fn_ty = ir.FunctionType(string_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "prefix"

    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    compare_loop_cond = func.append_basic_block("compare_loop_cond")
    compare_loop_body = func.append_basic_block("compare_loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")
    return_original = func.append_basic_block("return_original")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    prefix_data = builder.extract_value(func.args[1], 0, name="prefix_data")
    prefix_size = builder.extract_value(func.args[1], 1, name="prefix_size")
    builder.branch(size_check_block)

    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", prefix_size, str_size, name="size_ok")
    builder.cbranch(size_ok, compare_loop_cond, return_original)

    builder = ir.IRBuilder(compare_loop_cond)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, prefix_size, name="cond")
    builder.cbranch(cond, compare_loop_body, match_block)

    builder = ir.IRBuilder(compare_loop_body)
    str_ptr = builder.gep(str_data, [i_phi], name="str_ptr")
    prefix_ptr = builder.gep(prefix_data, [i_phi], name="prefix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    prefix_ch = builder.load(prefix_ptr, name="prefix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, prefix_ch, name="chars_equal")

    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, compare_loop_body)

    builder.cbranch(chars_equal, compare_loop_cond, mismatch_block)

    builder = ir.IRBuilder(mismatch_block)
    builder.branch(return_original)

    builder = ir.IRBuilder(match_block)
    new_size = builder.sub(str_size, prefix_size, name="new_size")
    new_size_i64 = builder.zext(new_size, i64, name="new_size_i64")
    result_data = builder.call(malloc, [new_size_i64], name="result_data")

    new_data_ptr = builder.gep(str_data, [prefix_size], name="new_data_ptr")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [result_data, new_data_ptr, new_size, is_volatile])

    result = build_string_struct(builder, string_type, result_data, new_size)
    builder.ret(result)

    builder = ir.IRBuilder(return_original)
    builder.ret(func.args[0])

    return func


def emit_string_strip_suffix(module: ir.Module) -> ir.Function:
    """Emit the string.strip_suffix() method.

    Removes suffix if present at the end of the string.
    If the string doesn't end with the suffix, returns the original string.

    Algorithm:
    1. Check if string ends with suffix (compare bytes from end)
    2. If matches: return substring from 0 to (str_size - suffix_size)
    3. If no match: return original string unchanged

    Examples:
        "hello".strip_suffix("lo") -> "hel"
        "hello".strip_suffix("x") -> "hello"
        "test".strip_suffix("testing") -> "test"
        "".strip_suffix("x") -> ""

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_strip_suffix({ i8*, i32 } str, { i8*, i32 } suffix)
    """
    func_name = "string_strip_suffix"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    fn_ty = ir.FunctionType(string_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"
    func.args[1].name = "suffix"

    entry_block = func.append_basic_block("entry")
    size_check_block = func.append_basic_block("size_check")
    compare_loop_cond = func.append_basic_block("compare_loop_cond")
    compare_loop_body = func.append_basic_block("compare_loop_body")
    mismatch_block = func.append_basic_block("mismatch")
    match_block = func.append_basic_block("match")
    return_original = func.append_basic_block("return_original")

    builder = ir.IRBuilder(entry_block)
    str_data = builder.extract_value(func.args[0], 0, name="str_data")
    str_size = builder.extract_value(func.args[0], 1, name="str_size")
    suffix_data = builder.extract_value(func.args[1], 0, name="suffix_data")
    suffix_size = builder.extract_value(func.args[1], 1, name="suffix_size")
    builder.branch(size_check_block)

    builder = ir.IRBuilder(size_check_block)
    size_ok = builder.icmp_unsigned("<=", suffix_size, str_size, name="size_ok")
    offset = builder.sub(str_size, suffix_size, name="offset")
    builder.cbranch(size_ok, compare_loop_cond, return_original)

    builder = ir.IRBuilder(compare_loop_cond)
    i_phi = builder.phi(i32, name="i")
    i_phi.add_incoming(ir.Constant(i32, 0), size_check_block)
    cond = builder.icmp_unsigned("<", i_phi, suffix_size, name="cond")
    builder.cbranch(cond, compare_loop_body, match_block)

    builder = ir.IRBuilder(compare_loop_body)
    str_index = builder.add(offset, i_phi, name="str_index")
    str_ptr = builder.gep(str_data, [str_index], name="str_ptr")
    suffix_ptr = builder.gep(suffix_data, [i_phi], name="suffix_ptr")
    str_ch = builder.load(str_ptr, name="str_ch")
    suffix_ch = builder.load(suffix_ptr, name="suffix_ch")
    chars_equal = builder.icmp_unsigned("==", str_ch, suffix_ch, name="chars_equal")

    i_next = builder.add(i_phi, ir.Constant(i32, 1), name="i_next")
    i_phi.add_incoming(i_next, compare_loop_body)

    builder.cbranch(chars_equal, compare_loop_cond, mismatch_block)

    builder = ir.IRBuilder(mismatch_block)
    builder.branch(return_original)

    builder = ir.IRBuilder(match_block)
    new_size = builder.sub(str_size, suffix_size, name="new_size")
    new_size_i64 = builder.zext(new_size, i64, name="new_size_i64")
    result_data = builder.call(malloc, [new_size_i64], name="result_data")

    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [result_data, str_data, new_size, is_volatile])

    result = build_string_struct(builder, string_type, result_data, new_size)
    builder.ret(result)

    builder = ir.IRBuilder(return_original)
    builder.ret(func.args[0])

    return func
