"""
Inline emission functions for file.lines() iterator.

This module contains minimal inline emission code needed for foreach loops
with file.lines(). This is a temporary solution until file.lines() is
fully ported to standalone IR generation.

NOTE: This is ONLY for file.lines() - all other file methods require
`use <io/files>` and use the stdlib IR.
"""

from typing import Any
import llvmlite.ir as ir
from semantics.ast import MethodCall


def _emit_readln(codegen: Any, expr: MethodCall, file_ptr: ir.Value) -> ir.Value:
    """Emit LLVM IR for file.readln() - read one line from file.

    This is only used by foreach loops with file.lines() iterator.
    Regular file.readln() calls require `use <io/files>`.

    Note: file_ptr comes from the iterator's data_ptr field which is typed as {i8*, i32}*
    but actually stores a FILE* (i8*), so we need to bitcast it back.
    """
    assert codegen.builder is not None
    assert codegen.runtime.libc_stdio.fgets is not None

    # file_ptr is a fat pointer value {i8*, i32}, but we stored FILE* (i8*) in field 0
    # Extract field 0 to get the actual FILE* pointer
    file_ptr_as_i8ptr = codegen.builder.extract_value(file_ptr, 0, name="file_ptr_extracted")

    # Allocate buffer for the line (1024 bytes should be enough for most lines)
    buffer_size = ir.Constant(ir.IntType(64), 1024)
    malloc_func = codegen.get_malloc_func()
    buffer = codegen.builder.call(malloc_func, [buffer_size])

    # Call fgets(buffer, 1024, file)
    size_i32 = ir.Constant(codegen.i32, 1024)
    result = codegen.builder.call(codegen.runtime.libc_stdio.fgets, [buffer, size_i32, file_ptr_as_i8ptr])

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
