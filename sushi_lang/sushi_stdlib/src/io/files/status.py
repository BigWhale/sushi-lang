"""File status methods IR generation."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_fclose


def generate_close(module: ir.Module) -> None:
    """Generate IR for file.close() -> ~"""
    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()

    fn_ty = ir.FunctionType(i32, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_close")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    fclose_fn = declare_fclose(module)

    null_ptr = ir.Constant(i8_ptr, None)
    is_null = builder.icmp_unsigned('==', file_ptr, null_ptr)

    close_block = builder.append_basic_block("file_close")
    merge_block = builder.append_basic_block("file_close_done")

    builder.cbranch(is_null, merge_block, close_block)

    builder.position_at_end(close_block)
    builder.call(fclose_fn, [file_ptr])
    builder.branch(merge_block)

    builder.position_at_end(merge_block)

    builder.ret(ir.Constant(i32, 0))


def generate_is_open(module: ir.Module) -> None:
    """Generate IR for file.is_open() -> bool"""
    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()

    fn_ty = ir.FunctionType(i32, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_is_open")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    null_ptr = ir.Constant(i8_ptr, None)
    is_not_null = builder.icmp_unsigned('!=', file_ptr, null_ptr)

    result = builder.zext(is_not_null, i32)

    builder.ret(result)
