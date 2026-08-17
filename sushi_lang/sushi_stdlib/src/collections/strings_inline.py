"""Inline emission functions for string operations needed during compilation."""

from typing import Any
import llvmlite.ir as ir


def emit_string_is_empty(codegen: Any, string_val: ir.Value) -> ir.Value:
    """Emit LLVM IR for string.is_empty() - check if string length is 0."""
    assert codegen.builder is not None

    length = codegen.builder.extract_value(string_val, 1, name="string_len")

    zero = ir.Constant(codegen.i32, 0)
    is_empty = codegen.builder.icmp_signed('==', length, zero)

    return is_empty


def emit_strcmp_intrinsic_inline(module: ir.Module) -> ir.Function:
    """Emit `i32 llvm_strcmp({i8*, i32} str1, {i8*, i32} str2)`."""
    from sushi_lang.sushi_stdlib.src.collections.strings.compiler.strcmp import emit_strcmp_intrinsic
    return emit_strcmp_intrinsic(module)


def emit_strlen_intrinsic_inline(module: ir.Module) -> ir.Function:
    """Emit `i32 llvm_strlen(i8* str)`."""
    from sushi_lang.sushi_stdlib.src.collections.strings.compiler.strlen import emit_strlen_intrinsic
    return emit_strlen_intrinsic(module)
