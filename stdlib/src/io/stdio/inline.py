"""
Inline emission functions for stdin.lines() iterator.

This module contains minimal inline emission code needed for foreach loops
with stdin.lines(). This is a temporary solution until stdin.lines() is
fully ported to standalone IR generation.

NOTE: This is ONLY for stdin.lines() - all other stdio methods require
`use <io/stdio>` and use the stdlib IR.
"""

from typing import Any
import llvmlite.ir as ir
from semantics.ast import MethodCall


def _emit_readln(codegen: Any, expr: MethodCall) -> ir.Value:
    """Emit LLVM IR for stdin.readln() - read one line from stdin.

    This is only used by foreach loops with stdin.lines() iterator.
    Regular stdin.readln() calls require `use <io/stdio>`.
    """
    assert codegen.builder is not None
    assert codegen.runtime.libc_stdio.fgets is not None
    assert codegen.runtime.libc_stdio.stdin_handle is not None

    # Allocate buffer for the line (1024 bytes should be enough for most lines)
    buffer_size = ir.Constant(ir.IntType(64), 1024)
    malloc_func = codegen.get_malloc_func()
    buffer = codegen.builder.call(malloc_func, [buffer_size])

    # Call fgets(buffer, 1024, stdin)
    size_i32 = ir.Constant(codegen.i32, 1024)
    stdin_ptr = codegen.builder.load(codegen.runtime.libc_stdio.stdin_handle)
    result = codegen.builder.call(codegen.runtime.libc_stdio.fgets, [buffer, size_i32, stdin_ptr])

    # Remove trailing newline if present
    strlen_result = codegen.builder.call(codegen.runtime.libc_strings.strlen, [buffer])

    # Check if length > 0
    zero = ir.Constant(codegen.i32, 0)
    has_chars = codegen.builder.icmp_signed('>', strlen_result, zero)

    # Variable to store final length (may be decremented if newline is removed)
    final_length = codegen.builder.alloca(codegen.i32, name="final_length")
    codegen.builder.store(strlen_result, final_length)

    with codegen.builder.if_then(has_chars):
        # Get pointer to last character (length - 1)
        one = ir.Constant(codegen.i32, 1)
        last_index = codegen.builder.sub(strlen_result, one)
        last_char_ptr = codegen.builder.gep(buffer, [last_index])
        last_char = codegen.builder.load(last_char_ptr)

        # Check if it's a newline
        newline = ir.Constant(codegen.i8, ord('\n'))
        is_newline = codegen.builder.icmp_signed('==', last_char, newline)

        with codegen.builder.if_then(is_newline):
            # Replace newline with null terminator
            null_char = ir.Constant(codegen.i8, 0)
            codegen.builder.store(null_char, last_char_ptr)
            # Decrement final length
            codegen.builder.store(last_index, final_length)

    # Convert C string to fat pointer, passing pre-computed length
    final_len_val = codegen.builder.load(final_length)
    from stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    return cstr_to_fat_pointer_with_len(codegen.builder, buffer, final_len_val)
