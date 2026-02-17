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
from sushi_lang.semantics.ast import MethodCall


def _emit_readln(codegen: Any, expr: MethodCall) -> ir.Value:
    """Emit LLVM IR for stdin.readln() - read one line from stdin.

    Uses POSIX getline() for dynamic buffer allocation.
    This is only used by foreach loops with stdin.lines() iterator.
    Regular stdin.readln() calls require `use <io/stdio>`.
    """
    assert codegen.builder is not None
    assert codegen.runtime.libc_stdio.getline is not None
    assert codegen.runtime.libc_stdio.stdin_handle is not None

    builder = codegen.builder
    i8_ptr = codegen.i8.as_pointer()
    i64 = ir.IntType(64)

    # Allocate lineptr (i8*) and n (i64) for getline
    lineptr_alloca = builder.alloca(i8_ptr, name="lineptr")
    n_alloca = builder.alloca(i64, name="n")
    builder.store(ir.Constant(i8_ptr, None), lineptr_alloca)
    builder.store(ir.Constant(i64, 0), n_alloca)

    # Call getline(&lineptr, &n, stdin) -> ssize_t (i64)
    stdin_ptr = builder.load(codegen.runtime.libc_stdio.stdin_handle)
    bytes_read = builder.call(
        codegen.runtime.libc_stdio.getline,
        [lineptr_alloca, n_alloca, stdin_ptr],
        name="bytes_read"
    )

    # Check if getline returned < 0 (EOF or error)
    zero_i64 = ir.Constant(i64, 0)
    is_eof = builder.icmp_signed('<', bytes_read, zero_i64, name="is_eof")

    eof_block = builder.append_basic_block(name="readln_eof")
    success_block = builder.append_basic_block(name="readln_success")
    builder.cbranch(is_eof, eof_block, success_block)

    # EOF path: free getline buffer (if any), return empty string
    builder.position_at_end(eof_block)
    eof_lineptr = builder.load(lineptr_alloca, name="eof_lineptr")
    eof_null = ir.Constant(i8_ptr, None)
    eof_has_buf = builder.icmp_unsigned('!=', eof_lineptr, eof_null, name="eof_has_buf")

    eof_free_block = builder.append_basic_block(name="readln_eof_free")
    eof_ret_block = builder.append_basic_block(name="readln_eof_ret")
    builder.cbranch(eof_has_buf, eof_free_block, eof_ret_block)

    builder.position_at_end(eof_free_block)
    free_func = codegen.get_free_func()
    builder.call(free_func, [eof_lineptr])
    builder.branch(eof_ret_block)

    builder.position_at_end(eof_ret_block)
    malloc_func = codegen.get_malloc_func()
    empty_buf = builder.call(malloc_func, [ir.Constant(i64, 1)], name="empty_buf")
    builder.store(ir.Constant(codegen.i8, 0), empty_buf)
    from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    empty_fat = cstr_to_fat_pointer_with_len(builder, empty_buf, ir.Constant(codegen.i32, 0))

    # We need a merge block after all paths for the final return
    merge_block = builder.append_basic_block(name="readln_merge")
    builder.branch(merge_block)

    # Success path: strip trailing \n and \r\n
    builder.position_at_end(success_block)
    lineptr_val = builder.load(lineptr_alloca, name="lineptr")
    len_i32 = builder.trunc(bytes_read, codegen.i32, name="len_i32")
    final_length = builder.alloca(codegen.i32, name="final_length")
    builder.store(len_i32, final_length)

    one_i32 = ir.Constant(codegen.i32, 1)
    has_chars = builder.icmp_signed('>', len_i32, ir.Constant(codegen.i32, 0), name="has_chars")

    check_newline_block = builder.append_basic_block(name="readln_check_nl")
    to_merge_block = builder.append_basic_block(name="readln_to_merge")
    builder.cbranch(has_chars, check_newline_block, to_merge_block)

    # Check and strip trailing \n
    builder.position_at_end(check_newline_block)
    cur_len = builder.load(final_length, name="cur_len")
    last_idx = builder.sub(cur_len, one_i32, name="last_idx")
    last_ptr = builder.gep(lineptr_val, [last_idx], name="last_ptr")
    last_char = builder.load(last_ptr, name="last_char")
    newline = ir.Constant(codegen.i8, ord('\n'))
    is_newline = builder.icmp_signed('==', last_char, newline, name="is_newline")

    strip_newline_block = builder.append_basic_block(name="readln_strip_nl")
    builder.cbranch(is_newline, strip_newline_block, to_merge_block)

    # Strip \n, then check for \r
    builder.position_at_end(strip_newline_block)
    null_byte = ir.Constant(codegen.i8, 0)
    builder.store(null_byte, last_ptr)
    new_len = builder.sub(cur_len, one_i32, name="new_len_no_lf")
    builder.store(new_len, final_length)

    has_more = builder.icmp_signed('>', new_len, ir.Constant(codegen.i32, 0), name="has_more")
    check_cr_block = builder.append_basic_block(name="readln_check_cr")
    builder.cbranch(has_more, check_cr_block, to_merge_block)

    builder.position_at_end(check_cr_block)
    cr_idx = builder.sub(new_len, one_i32, name="cr_idx")
    cr_ptr = builder.gep(lineptr_val, [cr_idx], name="cr_ptr")
    cr_char = builder.load(cr_ptr, name="cr_char")
    cr_byte = ir.Constant(codegen.i8, ord('\r'))
    is_cr = builder.icmp_signed('==', cr_char, cr_byte, name="is_cr")

    strip_cr_block = builder.append_basic_block(name="readln_strip_cr")
    builder.cbranch(is_cr, strip_cr_block, to_merge_block)

    builder.position_at_end(strip_cr_block)
    builder.store(null_byte, cr_ptr)
    len_no_crlf = builder.sub(new_len, one_i32, name="new_len_no_crlf")
    builder.store(len_no_crlf, final_length)
    builder.branch(to_merge_block)

    # Build fat pointer for success path and branch to merge
    builder.position_at_end(to_merge_block)
    final_len_val = builder.load(final_length, name="final_len")
    success_fat = cstr_to_fat_pointer_with_len(builder, lineptr_val, final_len_val)
    builder.branch(merge_block)

    # Merge block: phi node to select between EOF and success results
    builder.position_at_end(merge_block)
    i8_ptr_ty = codegen.i8.as_pointer()
    string_struct_ty = ir.LiteralStructType([i8_ptr_ty, codegen.i32])
    result_phi = builder.phi(string_struct_ty, name="readln_result")
    result_phi.add_incoming(empty_fat, eof_ret_block)
    result_phi.add_incoming(success_fat, to_merge_block)

    return result_phi
