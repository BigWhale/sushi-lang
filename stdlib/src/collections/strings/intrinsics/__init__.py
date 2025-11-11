"""
Intrinsic LLVM IR Functions for String Operations

This module provides low-level LLVM IR building blocks for string operations,
replacing C standard library dependencies with pure LLVM implementations.

All intrinsics are self-contained and generate standalone LLVM IR functions
that can be used by higher-level string methods.

Available Intrinsics (Fat Pointer Compatible):
- strcmp: Lexicographic string comparison
- utf8_count: Count UTF-8 characters in bounded byte range
- utf8_byte_offset: Find byte offset of nth UTF-8 character in bounded range
- char_ops: ASCII character operations (toupper/tolower/isspace)

Note: string_byte_count intrinsic removed - size is directly available from fat pointer!
"""

import llvmlite.ir as ir


def declare_utf8_count_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the UTF-8 character count intrinsic function.

    Counts UTF-8 characters by identifying continuation bytes in a bounded range.
    Works with fat pointer representation.

    Args:
        module: The LLVM module to declare the function in.

    Returns:
        The declared function: i32 llvm_utf8_count(i8* data, i32 size)
    """
    func_name = "llvm_utf8_count"

    # Check if already declared
    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_utf8_byte_offset_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the UTF-8 byte offset intrinsic function.

    Finds the byte offset of the nth UTF-8 character in a bounded range.
    Works with fat pointer representation.

    Args:
        module: The LLVM module to declare the function in.

    Returns:
        The declared function: i32 llvm_utf8_byte_offset(i8* data, i32 size, i32 char_index)
    """
    func_name = "llvm_utf8_byte_offset"

    # Check if already declared
    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i32, i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_toupper_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the ASCII toupper intrinsic function.

    Args:
        module: The LLVM module to declare the function in.

    Returns:
        The declared function: i32 llvm_toupper(i32 c)
    """
    func_name = "llvm_toupper"

    # Check if already declared
    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_tolower_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the ASCII tolower intrinsic function.

    Args:
        module: The LLVM module to declare the function in.

    Returns:
        The declared function: i32 llvm_tolower(i32 c)
    """
    func_name = "llvm_tolower"

    # Check if already declared
    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_isspace_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the ASCII isspace intrinsic function.

    Args:
        module: The LLVM module to declare the function in.

    Returns:
        The declared function: i8 llvm_isspace(i32 c)
    """
    func_name = "llvm_isspace"

    # Check if already declared
    if func_name in module.globals:
        return module.globals[func_name]

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i8, [i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_strcmp_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the string comparison intrinsic function.

    Args:
        module: The LLVM module to declare the function in.

    Returns:
        The declared function: i32 llvm_strcmp({ i8*, i32 } str1, { i8*, i32 } str2)
    """
    func_name = "llvm_strcmp"

    # Check if already declared
    if func_name in module.globals:
        return module.globals[func_name]

    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    string_type = ir.LiteralStructType([i8_ptr, i32])
    fn_ty = ir.FunctionType(i32, [string_type, string_type])
    return ir.Function(module, fn_ty, name=func_name)
