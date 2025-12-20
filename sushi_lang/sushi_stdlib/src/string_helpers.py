"""
String Helper Functions

Utilities for working with C strings and fat pointers in LLVM IR.
This module provides higher-level operations built on top of LLVM intrinsics.

Design: Single Responsibility - string-specific helper functions only.
"""

import llvmlite.ir as ir
from .libc_declarations import declare_malloc


def declare_strlen(module: ir.Module) -> ir.Function:
    """Declare strlen as external (implementation emitted during final compilation).

    Returns:
        The llvm_strlen function declaration: i32 llvm_strlen(i8* s)
    """
    func_name = "llvm_strlen"
    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr])
    return ir.Function(module, fn_ty, name=func_name)


# ==============================================================================
# String Constants
# ==============================================================================

def create_string_constant(module: ir.Module, builder: ir.IRBuilder, value: str, name: str = "str") -> ir.Value:
    """Create a global string constant and return a pointer to it.

    Args:
        module: The LLVM module to create the constant in.
        builder: The IR builder for creating GEP instructions.
        value: The string value to create.
        name: Optional name for the global constant.

    Returns:
        An i8* pointer to the null-terminated string.
    """
    # Create the string type (array of i8 with null terminator)
    str_bytes = bytearray(value.encode("utf-8") + b'\0')
    str_ty = ir.ArrayType(ir.IntType(8), len(str_bytes))

    # Create global constant
    global_str = ir.GlobalVariable(module, str_ty, name=module.get_unique_name(name))
    global_str.linkage = 'internal'
    global_str.global_constant = True
    global_str.initializer = ir.Constant(str_ty, str_bytes)

    # Return pointer to first element (GEP with two zero indices)
    i32 = ir.IntType(32)
    zero = ir.Constant(i32, 0)
    return builder.gep(global_str, [zero, zero], inbounds=True)


# ==============================================================================
# String Allocation
# ==============================================================================

def allocate_string_buffer(builder: ir.IRBuilder, malloc_fn: ir.Function, size: int) -> ir.Value:
    """Allocate a string buffer on the heap.

    Args:
        builder: The IR builder for creating instructions.
        malloc_fn: The malloc function to call.
        size: Size of the buffer in bytes (including null terminator).

    Returns:
        An i8* pointer to the allocated buffer.
    """
    i64 = ir.IntType(64)  # malloc takes size_t (i64 on 64-bit systems)
    size_val = ir.Constant(i64, size)
    return builder.call(malloc_fn, [size_val], name="str_buffer")


# ==============================================================================
# C String to Fat Pointer Conversion
# ==============================================================================

def cstr_to_fat_pointer(
    module: ir.Module,
    builder: ir.IRBuilder,
    c_str: ir.Value
) -> ir.Value:
    """Convert null-terminated C string to fat pointer struct {i8*, i32}.

    Args:
        module: The LLVM module (for declaring functions).
        builder: The IR builder for creating instructions.
        c_str: Null-terminated i8* from C function.

    Returns:
        Fat pointer struct {i8* data, i32 size}.
    """
    # Declare strlen
    strlen_fn = declare_strlen(module)

    # Call strlen to get size
    size_i64 = builder.call(strlen_fn, [c_str], name="str_len")

    # Truncate to i32 (strings won't be > 4GB)
    i32 = ir.IntType(32)
    size = builder.trunc(size_i64, i32, name="str_size")

    return cstr_to_fat_pointer_with_len(builder, c_str, size)


def cstr_to_fat_pointer_with_len(
    builder: ir.IRBuilder,
    c_str: ir.Value,
    length: ir.Value
) -> ir.Value:
    """Convert C string to fat pointer struct using pre-computed length.

    Args:
        builder: The IR builder for creating instructions.
        c_str: Null-terminated i8* from C function.
        length: Pre-computed i32 length of the string.

    Returns:
        Fat pointer struct {i8* data, i32 size}.
    """
    # Build fat pointer struct: {i8* data, i32 size}
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    string_struct_type = ir.LiteralStructType([i8_ptr, i32])
    undef_struct = ir.Constant(string_struct_type, ir.Undefined)
    struct_with_data = builder.insert_value(undef_struct, c_str, 0, name="str_with_data")
    struct_complete = builder.insert_value(struct_with_data, length, 1, name="str_complete")

    return struct_complete


# ==============================================================================
# Fat Pointer to C String Conversion
# ==============================================================================

def fat_pointer_to_cstr(
    module: ir.Module,
    builder: ir.IRBuilder,
    fat_ptr: ir.Value
) -> ir.Value:
    """Convert fat pointer struct {i8*, i32} to null-terminated C string.

    Args:
        module: The LLVM module (for declaring malloc/memcpy).
        builder: The IR builder for creating instructions.
        fat_ptr: Fat pointer struct {i8* data, i32 size}.

    Returns:
        Null-terminated i8* suitable for passing to C functions.
        Caller is responsible for freeing the memory.
    """
    from .libc_declarations import declare_malloc, declare_memcpy

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    malloc_fn = declare_malloc(module)
    memcpy_fn = declare_memcpy(module)

    # Extract data and size from fat pointer
    data = builder.extract_value(fat_ptr, 0, name="data")
    size = builder.extract_value(fat_ptr, 1, name="size")

    # Allocate buffer for null-terminated string (size + 1)
    one = ir.Constant(i32, 1)
    cstr_size = builder.add(size, one, name="cstr_size")
    cstr_size_i64 = builder.zext(cstr_size, i64, name="cstr_size_i64")
    cstr = builder.call(malloc_fn, [cstr_size_i64], name="cstr")

    # Copy data to cstr using llvm.memcpy intrinsic
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [cstr, data, size, is_volatile])

    # Add null terminator
    null_pos = builder.gep(cstr, [size], name="null_pos")
    builder.store(ir.Constant(i8, 0), null_pos)

    return cstr
