"""
Basic String Operations

Implements fundamental string methods that work with fat pointer representation:
- size(): Returns byte count (O(1) field access)
- len(): Returns UTF-8 character count (O(n) using utf8_count)
- is_empty(): Returns true if byte count is 0 (O(1))
- concat(): Concatenates two strings (allocates new string)
"""

import llvmlite.ir as ir
from ..intrinsics import declare_utf8_count_intrinsic
from ..common import declare_malloc, declare_memcpy, build_string_struct
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types


def emit_string_size(module: ir.Module) -> ir.Function:
    """Emit the string.size() method.

    Returns the byte count of a string (NOT character count).
    This is an O(1) operation - just extracts field 1 from the fat pointer.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 string_size({ i8*, i32 } str)
    """
    func_name = "string_size"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i32 string_size({ i8*, i32 } str)
    fn_ty = ir.FunctionType(i32, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Create entry block
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract size field (index 1) from fat pointer
    size = builder.extract_value(func.args[0], 1, name="size")
    builder.ret(size)

    return func


def emit_string_len(module: ir.Module) -> ir.Function:
    """Emit the string.len() method.

    Returns the UTF-8 character count of a string (NOT byte count).
    This is an O(n) operation - calls utf8_count intrinsic.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 string_len({ i8*, i32 } str)
    """
    func_name = "string_len"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Function signature: i32 string_len({ i8*, i32 } str)
    fn_ty = ir.FunctionType(i32, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Declare utf8_count intrinsic
    utf8_count = declare_utf8_count_intrinsic(module)

    # Create entry block
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract data pointer (index 0) and size (index 1)
    data = builder.extract_value(func.args[0], 0, name="data")
    size = builder.extract_value(func.args[0], 1, name="size")

    # Call utf8_count(data, size)
    char_count = builder.call(utf8_count, [data, size], name="char_count")
    builder.ret(char_count)

    return func


def emit_string_concat(module: ir.Module) -> ir.Function:
    """Emit the string.concat() method.

    Concatenates two strings by allocating new memory and copying both strings.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_concat({ i8*, i32 } str1, { i8*, i32 } str2)
    """
    func_name = "string_concat"

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

    # Function signature: { i8*, i32 } string_concat({ i8*, i32 } str1, { i8*, i32 } str2)
    fn_ty = ir.FunctionType(string_type, [string_type, string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str1"
    func.args[1].name = "str2"

    # Create entry block
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)

    # Extract data pointers and sizes from both fat pointer structs
    data1 = builder.extract_value(func.args[0], 0, name="data1")
    size1 = builder.extract_value(func.args[0], 1, name="size1")
    data2 = builder.extract_value(func.args[1], 0, name="data2")
    size2 = builder.extract_value(func.args[1], 1, name="size2")

    # Calculate total size (no null terminator needed)
    total_size = builder.add(size1, size2, name="total_size")

    # Allocate memory for the new string
    total_size_i64 = builder.zext(total_size, i64, name="total_size_i64")
    new_data = builder.call(malloc, [total_size_i64], name="new_data")

    # Copy first string using llvm.memcpy intrinsic
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [new_data, data1, size1, is_volatile])

    # Copy second string after first
    offset_ptr = builder.gep(new_data, [size1], name="offset_ptr")
    builder.call(memcpy, [offset_ptr, data2, size2, is_volatile])

    # Build and return fat pointer struct
    result = build_string_struct(builder, string_type, new_data, total_size)
    builder.ret(result)

    return func
