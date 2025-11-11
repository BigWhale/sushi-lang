"""
Type Conversion IR Generation

Functions for generating LLVM IR to convert types to strings.
These are high-level operations used by the runtime formatting system.

Design: Single Responsibility - only type-to-string conversions.
"""

import llvmlite.ir as ir
from .libc_declarations import declare_sprintf, declare_malloc
from .string_helpers import create_string_constant, allocate_string_buffer, cstr_to_fat_pointer


# ==============================================================================
# Constants
# ==============================================================================

# Format strings for sprintf (matches backend/runtime/constants.py)
FORMAT_STRINGS = {
    "i8": "%hhd",
    "i16": "%hd",
    "i32": "%d",
    "i64": "%lld",
    "u8": "%hhu",
    "u16": "%hu",
    "u32": "%u",
    "u64": "%llu",
    "f32": "%g",
    "f64": "%g",
    "str": "%s",
}

# Buffer sizes for string conversions
INT_BUFFER_SIZE = 32   # Enough for any 64-bit integer + null terminator
FLOAT_BUFFER_SIZE = 64 # Enough for any float with 6 decimal places + null terminator


# ==============================================================================
# Integer to String
# ==============================================================================

def emit_integer_to_string(
    module: ir.Module,
    builder: ir.IRBuilder,
    int_value: ir.Value,
    is_signed: bool,
    bit_width: int
) -> ir.Value:
    """Generate LLVM IR to convert an integer to a string using sprintf.

    This is a standalone version that doesn't depend on codegen infrastructure.

    Args:
        module: The LLVM module (for declaring functions).
        builder: The IR builder for creating instructions.
        int_value: The integer value to convert.
        is_signed: True for signed integers, False for unsigned.
        bit_width: Bit width of the integer type (8, 16, 32, or 64).

    Returns:
        Fat pointer struct {i8* data, i32 size} containing the string representation.
    """
    # Declare required functions
    sprintf_fn = declare_sprintf(module)
    malloc_fn = declare_malloc(module)

    # Choose appropriate format string
    if bit_width <= 16:
        if bit_width == 8:
            fmt_key = "i8" if is_signed else "u8"
        else:  # 16-bit
            fmt_key = "i16" if is_signed else "u16"
    elif bit_width == 32:
        fmt_key = "i32" if is_signed else "u32"
    else:  # 64-bit
        fmt_key = "i64" if is_signed else "u64"

    fmt_str = create_string_constant(module, builder, FORMAT_STRINGS[fmt_key], name=f"fmt_{fmt_key}")

    # Allocate buffer
    buffer = allocate_string_buffer(builder, malloc_fn, INT_BUFFER_SIZE)

    # Prepare value for sprintf (may need extension)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # sprintf expects at least i32 for integers due to C promotion rules
    if bit_width < 32:
        if is_signed:
            converted_value = builder.sext(int_value, i32)
        else:
            converted_value = builder.zext(int_value, i32)
    elif bit_width == 32:
        converted_value = int_value
    else:  # 64-bit
        converted_value = int_value

    # Call sprintf
    builder.call(sprintf_fn, [buffer, fmt_str, converted_value])

    # Convert C string to fat pointer struct
    return cstr_to_fat_pointer(module, builder, buffer)


# ==============================================================================
# Float to String
# ==============================================================================

def emit_float_to_string(
    module: ir.Module,
    builder: ir.IRBuilder,
    float_value: ir.Value,
    is_double: bool
) -> ir.Value:
    """Generate LLVM IR to convert a float to a string using sprintf.

    Args:
        module: The LLVM module (for declaring functions).
        builder: The IR builder for creating instructions.
        float_value: The float value to convert.
        is_double: True for f64, False for f32.

    Returns:
        Fat pointer struct {i8* data, i32 size} containing the string representation.
    """
    # Declare required functions
    sprintf_fn = declare_sprintf(module)
    malloc_fn = declare_malloc(module)

    # Choose format string
    fmt_key = "f64" if is_double else "f32"
    fmt_str = create_string_constant(module, builder, FORMAT_STRINGS[fmt_key], name=f"fmt_{fmt_key}")

    # Allocate buffer
    buffer = allocate_string_buffer(builder, malloc_fn, FLOAT_BUFFER_SIZE)

    # Extend f32 to f64 for sprintf (C variadic function requirement)
    if not is_double:
        f64 = ir.DoubleType()
        float_value = builder.fpext(float_value, f64)

    # Call sprintf
    builder.call(sprintf_fn, [buffer, fmt_str, float_value])

    # Convert C string to fat pointer struct
    return cstr_to_fat_pointer(module, builder, buffer)


# ==============================================================================
# Boolean to String
# ==============================================================================

def emit_bool_to_string(
    module: ir.Module,
    builder: ir.IRBuilder,
    bool_value: ir.Value
) -> ir.Value:
    """Generate LLVM IR to convert a boolean to a string.

    Args:
        module: The LLVM module (for declaring functions).
        builder: The IR builder for creating instructions.
        bool_value: The boolean value to convert (i1 or i8).

    Returns:
        Fat pointer struct {i8* data, i32 size} containing "true" or "false".
    """
    # Create string constants for "true" and "false" as C strings
    true_cstr = create_string_constant(module, builder, "true", name="str_true")
    false_cstr = create_string_constant(module, builder, "false", name="str_false")

    # Convert to i1 if needed
    i1 = ir.IntType(1)
    if bool_value.type != i1:
        bool_i1 = builder.trunc(bool_value, i1)
    else:
        bool_i1 = bool_value

    # Select appropriate C string
    selected_cstr = builder.select(bool_i1, true_cstr, false_cstr, name="bool_cstr")

    # Convert to fat pointer struct
    return cstr_to_fat_pointer(module, builder, selected_cstr)
