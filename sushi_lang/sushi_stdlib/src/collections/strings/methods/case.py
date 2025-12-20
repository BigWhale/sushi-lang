"""
Case Conversion String Operations

Implements ASCII case conversion methods for fat pointer strings:
- upper(): Convert all ASCII lowercase to uppercase
- lower(): Convert all ASCII uppercase to lowercase
- cap(): Capitalize first character, lowercase rest

Note: These are ASCII-only operations (UTF-8 unaware for case conversion)
"""

import llvmlite.ir as ir
from ..intrinsics.char_ops import emit_toupper_intrinsic, emit_tolower_intrinsic
from ..common import declare_malloc
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types
from sushi_lang.sushi_stdlib.src.ir_builders import IRLoopBuilder, IRStructBuilder


def emit_string_upper(module: ir.Module) -> ir.Function:
    """Emit the string.upper() method.

    Converts all ASCII lowercase characters to uppercase.
    Non-ASCII characters are copied unchanged.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_upper({ i8*, i32 } str)
    """
    func_name = "string_upper"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare toupper intrinsic and malloc
    toupper = emit_toupper_intrinsic(module)
    malloc_fn = declare_malloc(module)

    # Function signature: { i8*, i32 } string_upper({ i8*, i32 } str)
    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Extract data and size, then use builder for transformation loop
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)
    data, size = IRStructBuilder.extract_fat_pointer_fields(builder, func.args[0])

    IRLoopBuilder.build_char_transform_loop(
        func, builder, module, data, size, toupper, malloc_fn,
        i8, i32, i64, string_type
    )

    return func


def emit_string_lower(module: ir.Module) -> ir.Function:
    """Emit the string.lower() method.

    Converts all ASCII uppercase characters to lowercase.
    Non-ASCII characters are copied unchanged.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_lower({ i8*, i32 } str)
    """
    func_name = "string_lower"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare tolower intrinsic and malloc
    tolower = emit_tolower_intrinsic(module)
    malloc_fn = declare_malloc(module)

    # Function signature: { i8*, i32 } string_lower({ i8*, i32 } str)
    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Extract data and size, then use builder for transformation loop
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)
    data, size = IRStructBuilder.extract_fat_pointer_fields(builder, func.args[0])

    IRLoopBuilder.build_char_transform_loop(
        func, builder, module, data, size, tolower, malloc_fn,
        i8, i32, i64, string_type
    )

    return func


def emit_string_cap(module: ir.Module) -> ir.Function:
    """Emit the string.cap() method.

    Capitalizes the first ASCII character and lowercases the rest.
    Non-ASCII characters are handled as follows:
    - First character: copied unchanged if not ASCII
    - Rest: converted to lowercase if ASCII uppercase, otherwise copied unchanged

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: { i8*, i32 } string_cap({ i8*, i32 } str)
    """
    func_name = "string_cap"

    # Check if already defined
    if func_name in module.globals:
        func = module.globals[func_name]
        if not func.is_declaration:
            return func

    # Get common types
    i8, i8_ptr, i32, i64, string_type = get_string_types()

    # Declare external functions and intrinsics
    malloc = declare_malloc(module)
    toupper = emit_toupper_intrinsic(module)
    tolower = emit_tolower_intrinsic(module)

    # Function signature: { i8*, i32 } string_cap({ i8*, i32 } str)
    fn_ty = ir.FunctionType(string_type, [string_type])
    func = ir.Function(module, fn_ty, name=func_name)
    func.args[0].name = "str"

    # Entry block: allocate new string
    entry_block = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry_block)
    data, size = IRStructBuilder.extract_fat_pointer_fields(builder, func.args[0])

    # Allocate memory for new string
    size_i64 = builder.zext(size, i64, name="size_i64")
    new_data = builder.call(malloc, [size_i64], name="new_data")

    # Early return if empty
    zero = ir.Constant(i32, 0)
    is_empty = builder.icmp_unsigned("==", size, zero, name="is_empty")
    empty_return = func.append_basic_block("empty_return")
    first_char_block = func.append_basic_block("first_char")
    builder.cbranch(is_empty, empty_return, first_char_block)

    # Empty return: return empty string
    builder = ir.IRBuilder(empty_return)
    result = IRStructBuilder.build_fat_pointer(builder, string_type, new_data, size)
    builder.ret(result)

    # First character block: capitalize first character
    builder = ir.IRBuilder(first_char_block)
    first_ptr = builder.gep(data, [zero], name="first_ptr")
    first_ch = builder.load(first_ptr, name="first_ch")
    first_ch_i32 = builder.zext(first_ch, i32, name="first_ch_i32")
    upper_first_i32 = builder.call(toupper, [first_ch_i32], name="upper_first_i32")
    upper_first = builder.trunc(upper_first_i32, i8, name="upper_first")

    # Store capitalized first character
    new_first_ptr = builder.gep(new_data, [zero], name="new_first_ptr")
    builder.store(upper_first, new_first_ptr)

    # Use loop builder for remaining characters
    one = ir.Constant(i32, 1)
    loop_end_block = func.append_basic_block("loop_end")

    def lowercase_body(body_builder: ir.IRBuilder, i: ir.Value):
        # Load character
        src_ptr = body_builder.gep(data, [i], name="src_ptr")
        ch = body_builder.load(src_ptr, name="ch")

        # Convert to lowercase
        ch_i32 = body_builder.zext(ch, i32, name="ch_i32")
        lower_ch_i32 = body_builder.call(tolower, [ch_i32], name="lower_ch_i32")
        lower_ch = body_builder.trunc(lower_ch_i32, i8, name="lower_ch")

        # Store in destination
        dst_ptr = body_builder.gep(new_data, [i], name="dst_ptr")
        body_builder.store(lower_ch, dst_ptr)

    IRLoopBuilder.build_counting_loop(
        func, builder, one, size,
        lowercase_body, i32, loop_end_block
    )

    # Loop end: build and return fat pointer
    builder = ir.IRBuilder(loop_end_block)
    result = IRStructBuilder.build_fat_pointer(builder, string_type, new_data, size)
    builder.ret(result)

    return func
