"""Environment variable function implementations for Sushi sys/env module."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types, get_string_type, get_maybe_type
from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc

_platform_env = get_platform_module('env')

if typing.TYPE_CHECKING:
    pass


def generate_getenv(module: ir.Module) -> None:
    """Generate getenv function: getenv(string key) -> Maybe<string>"""
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    libc_getenv = _platform_env.declare_getenv(module)
    malloc_fn = declare_malloc(module)

    if "strlen" not in module.globals:
        strlen_fn_ty = ir.FunctionType(i64, [i8_ptr])
        libc_strlen = ir.Function(module, strlen_fn_ty, name="strlen")
    else:
        libc_strlen = module.globals["strlen"]

    # Maybe<string> type: {i32 tag, [2 x i64] data} (#300 phase 2)
    # data must hold a string fat pointer (16 bytes -> K=2 i64 words)
    maybe_string_type = get_maybe_type(string_type)

    # The key arrives already marshalled as a C string, and the CALLER frees it (#292). The
    # body used to marshal it here and free nothing.
    func_type = ir.FunctionType(maybe_string_type, [i8_ptr])
    func = ir.Function(module, func_type, name="sushi_getenv")

    key_cstr = func.args[0]
    key_cstr.name = "key"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result_ptr = builder.call(libc_getenv, [key_cstr], name="result_ptr")

    null_ptr = ir.Constant(i8_ptr, None)
    is_null = builder.icmp_unsigned('==', result_ptr, null_ptr, name="is_null")

    none_block = func.append_basic_block("none")
    some_block = func.append_basic_block("some")

    builder.cbranch(is_null, none_block, some_block)

    builder.position_at_end(none_block)
    none_tag = ir.Constant(i32, 1)  # tag = 1 for None
    none_value = ir.Constant(maybe_string_type, ir.Undefined)
    none_with_tag = builder.insert_value(none_value, none_tag, 0, name="none.tag")
    builder.ret(none_with_tag)

    builder.position_at_end(some_block)

    result_len_i64 = builder.call(libc_strlen, [result_ptr], name="result_len_i64")
    result_len = builder.trunc(result_len_i64, i32, name="result_len")

    string_buffer = builder.call(malloc_fn, [result_len_i64], name="string_buffer")

    from sushi_lang.sushi_stdlib.src.libc_declarations import declare_memcpy
    memcpy_fn = declare_memcpy(module)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [string_buffer, result_ptr, builder.zext(result_len, ir.IntType(64)), is_volatile])

    string_complete = cstr_to_fat_pointer_with_len(builder, string_buffer, result_len, owned=1)

    data_temp = builder.alloca(maybe_string_type.elements[1], name="data_temp")

    data_temp_string = builder.bitcast(data_temp, string_type.as_pointer(), name="data_temp_string")
    builder.store(string_complete, data_temp_string)

    packed_data = builder.load(data_temp, name="packed_data")

    some_tag = ir.Constant(i32, 0)  # tag = 0 for Some
    some_value = ir.Constant(maybe_string_type, ir.Undefined)
    some_with_tag = builder.insert_value(some_value, some_tag, 0, name="some.tag")
    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some.complete")

    builder.ret(some_complete)


def generate_setenv(module: ir.Module) -> None:
    """Generate setenv function: setenv(string key, string value) -> i32"""
    i8, i8_ptr, i32, i64 = get_basic_types()

    libc_setenv = _platform_env.declare_setenv(module)

    # Both arguments arrive already marshalled, and the CALLER frees them (#292).
    func_type = ir.FunctionType(i32, [i8_ptr, i8_ptr])
    func = ir.Function(module, func_type, name="sushi_setenv")

    key_cstr = func.args[0]
    value_cstr = func.args[1]
    key_cstr.name = "key"
    value_cstr.name = "value"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    overwrite = ir.Constant(i32, 1)
    result = builder.call(libc_setenv, [key_cstr, value_cstr, overwrite], name="setenv_result")

    builder.ret(result)
