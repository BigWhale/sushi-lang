"""
Environment variable function implementations for Sushi sys/env module.

Implements:
- getenv: Get environment variable (returns Maybe<string>)
- setenv: Set environment variable (returns Result<i32>)

All functions use POSIX getenv()/setenv() under the hood for consistency
and portability across Unix-like systems.
"""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types, get_string_type
from sushi_lang.sushi_stdlib.src.string_helpers import fat_pointer_to_cstr, cstr_to_fat_pointer_with_len
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc

# Get platform-specific env module (darwin, linux, windows, etc.)
_platform_env = get_platform_module('env')

if typing.TYPE_CHECKING:
    pass


def generate_getenv(module: ir.Module) -> None:
    """Generate getenv function: getenv(string key) -> Maybe<string>

    Retrieves the value of an environment variable.

    Args:
        key: String fat pointer {i8* data, i32 size} - the environment variable name

    Returns:
        Maybe<string> enum:
        - Maybe.Some(string): If variable exists
        - Maybe.None(): If variable does not exist

    Implementation:
        1. Convert key fat pointer to null-terminated C string
        2. Call libc getenv(key_cstr) -> i8* result
        3. Check if result is NULL:
           - If NULL: Return Maybe.None()
           - If not NULL:
             a. Call strlen(result) to get length
             b. Allocate new buffer for Sushi string
             c. Copy result to buffer using memcpy
             d. Build string fat pointer {buffer, length}
             e. Return Maybe.Some(string)

    Maybe<string> Layout:
        { i32 tag, [12 x i8] data }
        - tag = 0: Some, data contains string fat pointer {i8* ptr, i32 size}
        - tag = 1: None, data is unused
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    # Declare external functions
    libc_getenv = _platform_env.declare_getenv(module)
    malloc_fn = declare_malloc(module)

    # Declare C strlen: size_t strlen(const char* s)
    if "strlen" not in module.globals:
        strlen_fn_ty = ir.FunctionType(i64, [i8_ptr])
        libc_strlen = ir.Function(module, strlen_fn_ty, name="strlen")
    else:
        libc_strlen = module.globals["strlen"]

    # Maybe<string> type: {i32 tag, [12 x i8] data}
    # data must hold a string (12 bytes is enough for {i8*, i32} on 64-bit)
    maybe_string_data_size = 12
    maybe_string_type = ir.LiteralStructType([i32, ir.ArrayType(i8, maybe_string_data_size)])

    # Define function signature: sushi_getenv(string key) -> Maybe<string>
    func_type = ir.FunctionType(maybe_string_type, [string_type])
    func = ir.Function(module, func_type, name="sushi_getenv")

    key_param = func.args[0]
    key_param.name = "key"

    # Create entry block
    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Convert key fat pointer to null-terminated C string
    key_cstr = fat_pointer_to_cstr(module, builder, key_param)

    # Call getenv(key_cstr)
    result_ptr = builder.call(libc_getenv, [key_cstr], name="result_ptr")

    # Check if result is NULL
    null_ptr = ir.Constant(i8_ptr, None)
    is_null = builder.icmp_unsigned('==', result_ptr, null_ptr, name="is_null")

    # Create blocks
    none_block = func.append_basic_block("none")
    some_block = func.append_basic_block("some")

    builder.cbranch(is_null, none_block, some_block)

    # None block: Return Maybe.None()
    builder.position_at_end(none_block)
    none_tag = ir.Constant(i32, 1)  # tag = 1 for None
    none_value = ir.Constant(maybe_string_type, ir.Undefined)
    none_with_tag = builder.insert_value(none_value, none_tag, 0, name="none.tag")
    builder.ret(none_with_tag)

    # Some block: Build string and return Maybe.Some(string)
    builder.position_at_end(some_block)

    # Get length of result using strlen
    result_len_i64 = builder.call(libc_strlen, [result_ptr], name="result_len_i64")
    result_len = builder.trunc(result_len_i64, i32, name="result_len")

    # Allocate buffer for Sushi string and copy the data
    string_buffer = builder.call(malloc_fn, [result_len_i64], name="string_buffer")

    # Copy result to string_buffer using memcpy
    from sushi_lang.sushi_stdlib.src.libc_declarations import declare_memcpy
    memcpy_fn = declare_memcpy(module)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [string_buffer, result_ptr, result_len, is_volatile])

    # Build Sushi string fat pointer using helper
    string_complete = cstr_to_fat_pointer_with_len(builder, string_buffer, result_len)

    # Pack string into Maybe.Some
    # Create a temporary array to hold the string
    data_temp = builder.alloca(ir.ArrayType(i8, maybe_string_data_size), name="data_temp")

    # Bitcast to string pointer and store
    data_temp_string = builder.bitcast(data_temp, string_type.as_pointer(), name="data_temp_string")
    builder.store(string_complete, data_temp_string)

    # Load the packed bytes
    packed_data = builder.load(data_temp, name="packed_data")

    # Build Maybe.Some
    some_tag = ir.Constant(i32, 0)  # tag = 0 for Some
    some_value = ir.Constant(maybe_string_type, ir.Undefined)
    some_with_tag = builder.insert_value(some_value, some_tag, 0, name="some.tag")
    some_complete = builder.insert_value(some_with_tag, packed_data, 1, name="some.complete")

    builder.ret(some_complete)


def generate_setenv(module: ir.Module) -> None:
    """Generate setenv function: setenv(string key, string value) -> i32

    Sets an environment variable to a new value.

    Args:
        key: String fat pointer {i8* data, i32 size} - the environment variable name
        value: String fat pointer {i8* data, i32 size} - the new value

    Returns:
        i32: 0 on success, -1 on failure (wrapped in Result<i32> at semantic level)

    Implementation:
        1. Convert key and value fat pointers to null-terminated C strings
        2. Call libc setenv(key_cstr, value_cstr, 1) where 1 = overwrite existing
        3. Return the result directly (0 = success, -1 = failure)

    Error cases (setenv returns -1):
        - EINVAL: name is NULL, empty, or contains '='
        - ENOMEM: Insufficient memory
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    # Declare external functions
    libc_setenv = _platform_env.declare_setenv(module)

    # Define function signature: sushi_setenv(string key, string value) -> i32
    func_type = ir.FunctionType(i32, [string_type, string_type])
    func = ir.Function(module, func_type, name="sushi_setenv")

    key_param = func.args[0]
    value_param = func.args[1]
    key_param.name = "key"
    value_param.name = "value"

    # Create entry block
    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Convert fat pointers to null-terminated C strings using helper
    key_cstr = fat_pointer_to_cstr(module, builder, key_param)
    value_cstr = fat_pointer_to_cstr(module, builder, value_param)

    # Call setenv(key_cstr, value_cstr, 1) where 1 = overwrite
    overwrite = ir.Constant(i32, 1)
    result = builder.call(libc_setenv, [key_cstr, value_cstr, overwrite], name="setenv_result")

    builder.ret(result)
