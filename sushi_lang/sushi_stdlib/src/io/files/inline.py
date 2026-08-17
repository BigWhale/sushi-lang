"""Inline emission functions for file.lines() iterator."""

from typing import Any
import llvmlite.ir as ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.backend.memory.heap import emit_malloc


def _emit_readln(codegen: Any, expr: MethodCall, file_ptr: ir.Value) -> ir.Value:
    """Emit LLVM IR for file.readln() - read one line from file."""
    assert codegen.builder is not None
    assert codegen.runtime.libc_stdio.fgets is not None

    file_ptr_as_i8ptr = codegen.builder.extract_value(file_ptr, 0, name="file_ptr_extracted")

    buffer_size = ir.Constant(ir.IntType(64), 1024)
    buffer = emit_malloc(codegen, codegen.builder, buffer_size)

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

    strlen_result = codegen.builder.call(codegen.runtime.libc_strings.strlen, [buffer])

    zero = ir.Constant(codegen.i32, 0)
    has_chars = codegen.builder.icmp_signed('>', strlen_result, zero)

    final_length = codegen.builder.alloca(codegen.i32, name="final_length")
    codegen.builder.store(strlen_result, final_length)

    with codegen.builder.if_then(has_chars):
        one = ir.Constant(codegen.i32, 1)
        last_index = codegen.builder.sub(strlen_result, one)
        last_char_ptr = codegen.builder.gep(buffer, [last_index])
        last_char = codegen.builder.load(last_char_ptr)

        newline = ir.Constant(codegen.i8, ord('\n'))
        is_newline = codegen.builder.icmp_signed('==', last_char, newline)

        with codegen.builder.if_then(is_newline):
            null_char = ir.Constant(codegen.i8, 0)
            codegen.builder.store(null_char, last_char_ptr)
            codegen.builder.store(last_index, final_length)

    final_len_val = codegen.builder.load(final_length)
    from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    line_fat = cstr_to_fat_pointer_with_len(codegen.builder, buffer, final_len_val, owned=1)
    read_exit_bb = codegen.builder.block
    codegen.builder.branch(done_bb)

    codegen.builder.position_at_end(done_bb)
    result_phi = codegen.builder.phi(string_type, name="readln_line")
    result_phi.add_incoming(empty_line, eof_bb)
    result_phi.add_incoming(line_fat, read_exit_bb)
    return result_phi
