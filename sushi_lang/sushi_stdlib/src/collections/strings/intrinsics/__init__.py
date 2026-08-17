"""Intrinsic LLVM IR Functions for String Operations"""

import llvmlite.ir as ir


def declare_utf8_count_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the UTF-8 character count intrinsic function."""
    func_name = "llvm_utf8_count"

    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_utf8_byte_offset_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the UTF-8 byte offset intrinsic function."""
    func_name = "llvm_utf8_byte_offset"

    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i32, [i8_ptr, i32, i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_toupper_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the ASCII toupper intrinsic function."""
    func_name = "llvm_toupper"

    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_tolower_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the ASCII tolower intrinsic function."""
    func_name = "llvm_tolower"

    if func_name in module.globals:
        return module.globals[func_name]

    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i32, [i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_isspace_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the ASCII isspace intrinsic function."""
    func_name = "llvm_isspace"

    if func_name in module.globals:
        return module.globals[func_name]

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    fn_ty = ir.FunctionType(i8, [i32])
    return ir.Function(module, fn_ty, name=func_name)


def declare_strcmp_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the string comparison intrinsic function."""
    func_name = "llvm_strcmp"

    if func_name in module.globals:
        return module.globals[func_name]

    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    string_type = ir.LiteralStructType([i8_ptr, i32, ir.IntType(8)])  # {data, size, owned} (#145)
    fn_ty = ir.FunctionType(i32, [string_type, string_type])
    return ir.Function(module, fn_ty, name=func_name)
