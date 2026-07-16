"""
I/O Iterator Builders

Builds the streaming iterator for stdin.lines(). file.lines() has its own
emitter (io/files/read.py, sushi_file_lines) and does not route through here.
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_iterator_type, get_string_type


# ==============================================================================
# Streaming Iterator Builders
# ==============================================================================

def build_stdin_lines_iterator(module: ir.Module) -> ir.Function:
    """Build stdin.lines() -> Iterator<string> function.

    Creates an iterator that reads lines from stdin on-demand.
    Uses sentinel length=-1 to indicate streaming mode.
    data_ptr is NULL (stdin is accessed via global handle).

    Args:
        module: LLVM module to add function to

    Returns:
        The generated function
    """
    # Types
    i32 = ir.IntType(32)
    string_fat_ptr = get_string_type()
    iterator_ty = get_iterator_type(string_fat_ptr)

    # Function signature: Iterator<string> @sushi_stdin_lines()
    fn_ty = ir.FunctionType(iterator_ty, [])
    func = ir.Function(module, fn_ty, name="sushi_stdin_lines")

    # Entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Constants
    zero = ir.Constant(i32, 0)
    minus_one = ir.Constant(i32, -1)
    null_ptr = ir.Constant(string_fat_ptr.as_pointer(), None)

    # Build iterator struct: {index=0, length=-1, data_ptr=NULL}
    undef_struct = ir.Constant(iterator_ty, ir.Undefined)
    struct_with_index = builder.insert_value(undef_struct, zero, 0, name="with_index")
    struct_with_length = builder.insert_value(struct_with_index, minus_one, 1, name="with_length")
    iterator = builder.insert_value(struct_with_length, null_ptr, 2, name="iterator")

    builder.ret(iterator)
    return func
