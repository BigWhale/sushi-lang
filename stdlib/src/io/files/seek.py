"""
File seeking methods IR generation.

Implements IR generation for:
- seek(i64, SeekFrom) - Seek to position in file
- tell() - Get current file position
"""

import llvmlite.ir as ir
from stdlib.src.libc_declarations import declare_fseek, declare_ftell


def generate_seek(module: ir.Module) -> None:
    """Generate IR for file.seek(i64, SeekFrom) -> ~

    Seeks to position in file.
    SeekFrom enum: Start (0), Current (1), End (2)
    Maps to C constants: SEEK_SET (0), SEEK_CUR (1), SEEK_END (2)

    SeekFrom enum struct: {i32 tag, [N x i8] data}
    We only need the tag field.
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()

    # SeekFrom enum struct type (simplified - we only care about the tag)
    # The actual struct size depends on the largest variant, but we can just use
    # a pointer and extract the tag field
    seekfrom_struct_ty = ir.LiteralStructType([i32, ir.ArrayType(i8, 0)])

    # Function signature: i32 @sushi_file_seek(ptr %file_ptr, i64 %offset, ptr %seekfrom)
    fn_ty = ir.FunctionType(i32, [i8_ptr, i64, seekfrom_struct_ty.as_pointer()])
    fn = ir.Function(module, fn_ty, name="sushi_file_seek")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get parameters
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"
    offset_val = fn.args[1]
    offset_val.name = "offset"
    seekfrom_ptr = fn.args[2]
    seekfrom_ptr.name = "seekfrom"

    # Declare fseek
    fseek_fn = declare_fseek(module)

    # Extract tag from SeekFrom enum
    zero = ir.Constant(i32, 0)
    tag_ptr = builder.gep(seekfrom_ptr, [zero, zero])
    tag = builder.load(tag_ptr)

    # Map SeekFrom tag to C SEEK_* constants
    # SeekFrom.Start (0) -> SEEK_SET (0)
    # SeekFrom.Current (1) -> SEEK_CUR (1)
    # SeekFrom.End (2) -> SEEK_END (2)
    # The mapping is identity, but we do it explicitly for clarity

    # Allocate space for whence value
    whence_ptr = builder.alloca(i32, name="seek_whence")

    # Create basic blocks for switch
    start_block = builder.append_basic_block("seek_start")
    current_block = builder.append_basic_block("seek_current")
    end_block = builder.append_basic_block("seek_end")
    call_block = builder.append_basic_block("seek_call")

    # Switch on enum tag
    switch = builder.switch(tag, call_block)
    switch.add_case(ir.Constant(i32, 0), start_block)
    switch.add_case(ir.Constant(i32, 1), current_block)
    switch.add_case(ir.Constant(i32, 2), end_block)

    # Case 0: SeekFrom.Start -> SEEK_SET (0)
    builder.position_at_end(start_block)
    builder.store(ir.Constant(i32, 0), whence_ptr)
    builder.branch(call_block)

    # Case 1: SeekFrom.Current -> SEEK_CUR (1)
    builder.position_at_end(current_block)
    builder.store(ir.Constant(i32, 1), whence_ptr)
    builder.branch(call_block)

    # Case 2: SeekFrom.End -> SEEK_END (2)
    builder.position_at_end(end_block)
    builder.store(ir.Constant(i32, 2), whence_ptr)
    builder.branch(call_block)

    # Call fseek
    builder.position_at_end(call_block)
    whence = builder.load(whence_ptr)
    builder.call(fseek_fn, [file_ptr, offset_val, whence])

    # Return blank value (i32 0)
    builder.ret(ir.Constant(i32, 0))


def generate_tell(module: ir.Module) -> None:
    """Generate IR for file.tell() -> i64

    Returns current file position.
    """
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()

    # Function signature: i64 @sushi_file_tell(ptr %file_ptr)
    fn_ty = ir.FunctionType(i64, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_tell")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get file pointer parameter
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    # Declare ftell
    ftell_fn = declare_ftell(module)

    # Call ftell(file_ptr)
    position = builder.call(ftell_fn, [file_ptr])

    # Return position
    builder.ret(position)
