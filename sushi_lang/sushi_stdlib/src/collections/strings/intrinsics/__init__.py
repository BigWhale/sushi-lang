"""Intrinsic LLVM IR Functions for String Operations"""

import llvmlite.ir as ir

from sushi_lang.sushi_stdlib.src.libc_declarations import declare_extern

_i8 = ir.IntType(8)
_i32 = ir.IntType(32)


def declare_utf8_count_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the UTF-8 character count intrinsic function."""
    return declare_extern(module, "llvm_utf8_count", _i32, [_i8.as_pointer(), _i32])


def declare_utf8_byte_offset_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the UTF-8 byte offset intrinsic function."""
    return declare_extern(module, "llvm_utf8_byte_offset", _i32, [_i8.as_pointer(), _i32, _i32])


def declare_isspace_intrinsic(module: ir.Module) -> ir.Function:
    """Declare the ASCII isspace intrinsic function."""
    return declare_extern(module, "llvm_isspace", _i8, [_i32])
