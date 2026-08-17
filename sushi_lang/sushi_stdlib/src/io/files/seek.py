"""File seeking methods IR generation."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_fseek, declare_ftell
from sushi_lang.sushi_stdlib.src.type_definitions import get_unit_enum_type


def generate_seek(module: ir.Module) -> None:
    """Generate IR for file.seek(i64, SeekFrom) -> ~"""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()

    seekfrom_struct_ty = get_unit_enum_type()

    fn_ty = ir.FunctionType(i32, [i8_ptr, i64, seekfrom_struct_ty.as_pointer()])
    fn = ir.Function(module, fn_ty, name="sushi_file_seek")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    offset_val = fn.args[1]
    offset_val.name = "offset"
    seekfrom_ptr = fn.args[2]
    seekfrom_ptr.name = "seekfrom"

    fseek_fn = declare_fseek(module)

    zero = ir.Constant(i32, 0)
    tag_ptr = builder.gep(seekfrom_ptr, [zero, zero])
    tag = builder.load(tag_ptr)

    # Map SeekFrom tag to C SEEK_* constants
    # SeekFrom.Start (0) -> SEEK_SET (0)
    # SeekFrom.Current (1) -> SEEK_CUR (1)
    # SeekFrom.End (2) -> SEEK_END (2)
    # The mapping is identity, but we do it explicitly for clarity

    whence_ptr = builder.alloca(i32, name="seek_whence")

    start_block = builder.append_basic_block("seek_start")
    current_block = builder.append_basic_block("seek_current")
    end_block = builder.append_basic_block("seek_end")
    call_block = builder.append_basic_block("seek_call")

    switch = builder.switch(tag, call_block)
    switch.add_case(ir.Constant(i32, 0), start_block)
    switch.add_case(ir.Constant(i32, 1), current_block)
    switch.add_case(ir.Constant(i32, 2), end_block)

    builder.position_at_end(start_block)
    builder.store(ir.Constant(i32, 0), whence_ptr)
    builder.branch(call_block)

    builder.position_at_end(current_block)
    builder.store(ir.Constant(i32, 1), whence_ptr)
    builder.branch(call_block)

    builder.position_at_end(end_block)
    builder.store(ir.Constant(i32, 2), whence_ptr)
    builder.branch(call_block)

    builder.position_at_end(call_block)
    whence = builder.load(whence_ptr)
    builder.call(fseek_fn, [file_ptr, offset_val, whence])

    builder.ret(ir.Constant(i32, 0))


def generate_tell(module: ir.Module) -> None:
    """Generate IR for file.tell() -> i64"""
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()

    fn_ty = ir.FunctionType(i64, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_tell")

    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    ftell_fn = declare_ftell(module)

    position = builder.call(ftell_fn, [file_ptr])

    builder.ret(position)
