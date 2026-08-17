"""Basic String Operations"""

import llvmlite.ir as ir
from ..intrinsics import declare_utf8_count_intrinsic
from ..common import declare_malloc, declare_memcpy, build_string_struct
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types


def emit_string_size(module: ir.Module) -> ir.Function:
    """Emit `i32 string_size({i8*, i32} str)`."""
    func_name = "string_size"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    fn_ty = ir.FunctionType(i32, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    size = builder.extract_value(func.args[0], 1, name="size")
    builder.ret(size)

    return func


def emit_string_len(module: ir.Module) -> ir.Function:
    """Emit `i32 string_len({i8*, i32} str)`."""
    func_name = "string_len"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    fn_ty = ir.FunctionType(i32, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    utf8_count = declare_utf8_count_intrinsic(module)

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    char_count = builder.call(utf8_count, [data, size], name="char_count")
    builder.ret(char_count)

    return func


def emit_string_concat(module: ir.Module) -> ir.Function:
    """Emit `{i8*, i32} string_concat({i8*, i32} str1, {i8*, i32} str2)`."""
    func_name = "string_concat"

    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    i8, i8_ptr, i32, i64, string_type = get_string_types()

    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)

    fn_ty = ir.FunctionType(string_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str1"
    func.args[1].name = "str2"

    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    data1 = builder.extract_value(func.args[0], 0, name="data1")
    size1 = builder.extract_value(func.args[0], 1, name="size1")
    data2 = builder.extract_value(func.args[1], 0, name="data2")
    size2 = builder.extract_value(func.args[1], 1, name="size2")

    total_size = builder.add(size1, size2, name="total_size")

    total_size_i64 = builder.zext(total_size, i64, name="total_size_i64")
    new_data = builder.call(malloc, [total_size_i64], name="new_data")

    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [new_data, data1, builder.zext(size1, ir.IntType(64)), is_volatile])

    offset_ptr = builder.gep(new_data, [size1], name="offset_ptr")
    builder.call(memcpy, [offset_ptr, data2, builder.zext(size2, ir.IntType(64)), is_volatile])

    result = build_string_struct(builder, string_type, new_data, total_size, owned=1)
    builder.ret(result)

    return func
