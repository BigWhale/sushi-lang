"""Inline emission functions for string operations needed during compilation."""

import llvmlite.ir as ir


def emit_strlen_intrinsic_inline(module: ir.Module) -> ir.Function:
    """Emit `i32 llvm_strlen(i8* str)`."""
    from sushi_lang.sushi_stdlib.src.collections.strings.compiler.strlen import emit_strlen_intrinsic
    return emit_strlen_intrinsic(module)
