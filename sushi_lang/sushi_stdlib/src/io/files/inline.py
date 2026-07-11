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
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.backend.memory.heap import emit_malloc


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
    buffer = emit_malloc(codegen, codegen.builder, buffer_size)

    # Call fgets(buffer, 1024, file)
    size_i32 = ir.Constant(codegen.i32, 1024)
    result = codegen.builder.call(codegen.runtime.libc_stdio.fgets, [buffer, size_i32, file_ptr_as_i8ptr])

    # EOF / error: fgets returns NULL and leaves the buffer UNCHANGED (undefined contents).
    # The buffer must NOT be read as a line -- doing so strlen's uninitialized memory and, now
    # that RAII frees each line buffer, that memory is recently-freed garbage, so EOF would be
    # misdetected and the loop would read junk forever. Free the buffer and return an empty,
    # unowned string {null, 0, owned=0} so the foreach's is_empty check ends the loop (#145).
    from sushi_lang.sushi_stdlib.src.collections.strings.common import build_string_struct
    string_type = codegen.types.string_struct
    free_func = codegen.get_free_func()
    is_eof = codegen.builder.icmp_unsigned('==', result, ir.Constant(result.type, None))
    eof_bb = codegen.func.append_basic_block(name="readln.eof")
    read_bb = codegen.func.append_basic_block(name="readln.read")
    done_bb = codegen.func.append_basic_block(name="readln.done")
    codegen.builder.cbranch(is_eof, eof_bb, read_bb)

    codegen.builder.position_at_end(eof_bb)
    codegen.builder.call(free_func, [buffer])
    empty_line = build_string_struct(codegen.builder, string_type,
                                     ir.Constant(codegen.i8.as_pointer(), None),
                                     ir.Constant(codegen.i32, 0), owned=0)
    codegen.builder.branch(done_bb)

    codegen.builder.position_at_end(read_bb)

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

    # Convert C string to fat pointer, passing pre-computed length (heap line -> owned=1)
    final_len_val = codegen.builder.load(final_length)
    from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    line_fat = cstr_to_fat_pointer_with_len(codegen.builder, buffer, final_len_val, owned=1)
    read_exit_bb = codegen.builder.block
    codegen.builder.branch(done_bb)

    # Merge the EOF (empty) and read (line) paths.
    codegen.builder.position_at_end(done_bb)
    result_phi = codegen.builder.phi(string_type, name="readln_line")
    result_phi.add_incoming(empty_line, eof_bb)
    result_phi.add_incoming(line_fat, read_exit_bb)
    return result_phi
