"""
Inline emission functions for string operations needed during compilation.

This module contains minimal inline emission code needed for:
1. string.is_empty() in .lines() iterators (foreach loops)
2. strcmp intrinsic for HashMap<string, V> key comparison
3. strlen intrinsic for converting C strings to fat pointers

These are emitted directly into the compiled module rather than loaded from sushi_lang.sushi_stdlib.

NOTE: string.is_empty() no longer requires `use <collections/strings>` - it's now inline-only.
"""

from typing import Any
import llvmlite.ir as ir


def emit_string_is_empty(codegen: Any, string_val: ir.Value) -> ir.Value:
    """Emit LLVM IR for string.is_empty() - check if string length is 0.

    This is only used by foreach loops with .lines() iterators.
    For regular .is_empty() calls, the compiler now uses the inline intrinsic.

    Note: string_val is a fat pointer {i8*, i32}, so we extract the length field directly.
    """
    assert codegen.builder is not None

    # Extract length field from fat pointer struct (field 1)
    length = codegen.builder.extract_value(string_val, 1, name="string_len")

    # Compare with zero
    zero = ir.Constant(codegen.i32, 0)
    is_empty = codegen.builder.icmp_signed('==', length, zero)

    return is_empty


def emit_strcmp_intrinsic_inline(module: ir.Module) -> ir.Function:
    """Emit strcmp intrinsic directly into the compilation module.

    Used by HashMap<string, V> for key comparison without requiring stdlib linkage.
    Emits the same LLVM IR as stdlib/src/collections/strings/compiler/strcmp.py
    but generates it inline during compilation.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 llvm_strcmp({ i8*, i32 } str1, { i8*, i32 } str2)
    """
    from sushi_lang.sushi_stdlib.src.collections.strings.compiler.strcmp import emit_strcmp_intrinsic
    return emit_strcmp_intrinsic(module)


def emit_strlen_intrinsic_inline(module: ir.Module) -> ir.Function:
    """Emit strlen intrinsic directly into the compilation module.

    Used for converting C strings (from cmdline args, fgets, etc.) to fat pointers.
    Emits the same LLVM IR as stdlib/src/collections/strings/compiler/strlen.py
    but generates it inline during compilation.

    Args:
        module: The LLVM module to emit the function into.

    Returns:
        The emitted function: i32 llvm_strlen(i8* str)
    """
    from sushi_lang.sushi_stdlib.src.collections.strings.compiler.strlen import emit_strlen_intrinsic
    return emit_strlen_intrinsic(module)
