"""
I/O Iterator Builders

Shared utilities for building streaming iterators for stdin and file I/O.
Eliminates duplication between stdin.lines() and file.lines() implementations.

Design: DRY - single implementation for all streaming I/O iterators.
REFACTORED: Now uses centralized type_definitions module.
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc
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


def build_file_lines_iterator(module: ir.Module) -> ir.Function:
    """Build file.lines() -> Iterator<string> function.

    Creates an iterator that reads lines from a file on-demand.
    Uses sentinel length=-1 to indicate streaming mode.
    data_ptr stores the FILE* pointer on the heap for persistence.

    Args:
        module: LLVM module to add function to

    Returns:
        The generated function
    """
    # Declare malloc
    malloc_fn = declare_malloc(module)

    # Types
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    string_fat_ptr = get_string_type()
    iterator_ty = get_iterator_type(string_fat_ptr)

    # Function signature: Iterator<string> @sushi_file_lines(i8* %file_ptr)
    fn_ty = ir.FunctionType(iterator_ty, [i8_ptr])
    func = ir.Function(module, fn_ty, name="sushi_file_lines")
    file_ptr_arg = func.args[0]
    file_ptr_arg.name = "file_ptr"

    # Entry block
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    # Constants
    zero = ir.Constant(i32, 0)
    minus_one = ir.Constant(i32, -1)
    ptr_size = ir.Constant(i64, 8)  # Size of a pointer

    # Allocate storage for FILE* pointer on heap (persists beyond function call)
    file_ptr_storage = builder.call(malloc_fn, [ptr_size], name="file_ptr_storage")
    file_ptr_storage_typed = builder.bitcast(file_ptr_storage, i8_ptr.as_pointer(), name="file_ptr_ptr")

    # Store FILE* into allocated storage
    builder.store(file_ptr_arg, file_ptr_storage_typed)

    # Build iterator struct: {index=0, length=-1, data_ptr=file_ptr_storage}
    # Cast i8** to string_fat_ptr* for type compatibility
    data_ptr_casted = builder.bitcast(file_ptr_storage_typed, string_fat_ptr.as_pointer(), name="data_ptr")

    undef_struct = ir.Constant(iterator_ty, ir.Undefined)
    struct_with_index = builder.insert_value(undef_struct, zero, 0, name="with_index")
    struct_with_length = builder.insert_value(struct_with_index, minus_one, 1, name="with_length")
    iterator = builder.insert_value(struct_with_length, data_ptr_casted, 2, name="iterator")

    builder.ret(iterator)
    return func


# ==============================================================================
# Array Iterator Builders (for future use)
# ==============================================================================

def build_array_iterator(
    builder: ir.IRBuilder,
    iterator_ty: ir.LiteralStructType,
    array_data_ptr: ir.Value,
    array_length: ir.Value,
    i32: ir.IntType
) -> ir.Value:
    """Build an iterator for array iteration.

    Creates a standard array iterator with actual length and data pointer.
    This is used for regular arrays (not streaming I/O).

    Args:
        builder: IR builder
        iterator_ty: Iterator struct type
        array_data_ptr: Pointer to array data
        array_length: Length of array
        i32: i32 type

    Returns:
        Iterator struct value
    """
    zero = ir.Constant(i32, 0)

    # Build iterator struct: {index=0, length=array_length, data_ptr=array_data_ptr}
    undef_struct = ir.Constant(iterator_ty, ir.Undefined)
    struct_with_index = builder.insert_value(undef_struct, zero, 0, name="with_index")
    struct_with_length = builder.insert_value(struct_with_index, array_length, 1, name="with_length")
    iterator = builder.insert_value(struct_with_length, array_data_ptr, 2, name="iterator")

    return iterator
