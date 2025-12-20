"""
File reading methods IR generation.

Implements IR generation for:
- read() - Read entire file as string
- readln() - Read one line
- readch() - Read one character
- lines() - Create line iterator
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src.io.files.common import (
    allocate_and_read_full_file,
    allocate_and_read_line,
    allocate_and_read_char
)
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc


def generate_read(module: ir.Module) -> None:
    """Generate IR for file.read() -> string

    Reads entire file contents as a string.
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: {ptr, i32} @sushi_file_read(ptr %file_ptr)
    fn_ty = ir.FunctionType(string_struct_type, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_read")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get file pointer parameter
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    # Read entire file using common helper (returns fat pointer)
    result = allocate_and_read_full_file(module, builder, file_ptr)

    builder.ret(result)


def generate_readln(module: ir.Module) -> None:
    """Generate IR for file.readln() -> string

    Reads one line from file (removes trailing newline).
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: {ptr, i32} @sushi_file_readln(ptr %file_ptr)
    fn_ty = ir.FunctionType(string_struct_type, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_readln")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get file pointer parameter
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    # Read one line using common helper (returns fat pointer)
    result = allocate_and_read_line(module, builder, file_ptr)

    builder.ret(result)


def generate_readch(module: ir.Module) -> None:
    """Generate IR for file.readch() -> string

    Reads one character from file (returns empty string on EOF).
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()
    string_struct_type = ir.LiteralStructType([i8_ptr, i32])

    # Function signature: {ptr, i32} @sushi_file_readch(ptr %file_ptr)
    fn_ty = ir.FunctionType(string_struct_type, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_readch")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get file pointer parameter
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    # Read one character using common helper (returns fat pointer)
    result = allocate_and_read_char(module, builder, file_ptr)

    builder.ret(result)


def generate_lines(module: ir.Module) -> None:
    """Generate IR for file.lines() -> Iterator<string>

    Returns an iterator that reads lines from the file.
    Iterator struct: {i32 index, i32 length, {i8*,i32}** data_ptr}
    - index: always 0 (not used for file iteration)
    - length: -1 (sentinel value indicating streaming iterator)
    - data_ptr: pointer to FILE* pointer (heap-allocated to outlive function call)

    Note: Strings are fat pointers {i8*, i32}, so the iterator's data_ptr field
    is typed as a pointer to pointers to fat pointer structs. However, we actually
    store the FILE* pointer here (as i8**), which works because the foreach loop
    uses the sentinel length value to determine this is a streaming iterator.

    The FILE* pointer must be stored on the heap because the iterator struct is
    used across multiple loop iterations in foreach loops.
    """
    # Declare external functions
    malloc_fn = declare_malloc(module)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()
    string_fat_ptr = ir.LiteralStructType([i8_ptr, i32])

    # Iterator struct type: {i32, i32, {i8*, i32}*}
    # For streaming iterators, we store FILE* casted to this type
    iterator_struct_ty = ir.LiteralStructType([i32, i32, string_fat_ptr.as_pointer()])

    # Function signature: {i32, i32, {i8*, i32}*} @sushi_file_lines(ptr %file_ptr)
    fn_ty = ir.FunctionType(iterator_struct_ty, [i8_ptr])
    fn = ir.Function(module, fn_ty, name="sushi_file_lines")

    # Create entry block
    bb = fn.append_basic_block("entry")
    builder = ir.IRBuilder(bb)

    # Get file pointer parameter
    file_ptr = fn.args[0]
    file_ptr.name = "file_ptr"

    # Allocate iterator on stack
    iterator_slot = builder.alloca(iterator_struct_ty, name="file_lines_iter")

    # For streaming file iterators, we need to store the FILE* pointer
    # Create a "fake" fat pointer by storing FILE* in field 0 and 0 in field 1
    # Allocate a fat pointer on the heap to store this
    fat_ptr_size = ir.Constant(i64, 16)  # Size of {i8*, i32} = 8 + 4 (with padding)
    file_ptr_storage = builder.call(malloc_fn, [fat_ptr_size], name="file_ptr_storage")
    file_ptr_storage_typed = builder.bitcast(file_ptr_storage, string_fat_ptr.as_pointer(), name="file_ptr_ptr")

    # Store FILE* in field 0 of the fat pointer
    zero = ir.Constant(i32, 0)
    one = ir.Constant(i32, 1)
    field0_ptr = builder.gep(file_ptr_storage_typed, [zero, zero])
    builder.store(file_ptr, field0_ptr)

    # Store 0 in field 1 (unused)
    field1_ptr = builder.gep(file_ptr_storage_typed, [zero, one])
    builder.store(zero, field1_ptr)

    # Initialize iterator fields
    # Set index = 0 (not used, but keep consistent)
    index_ptr = builder.gep(iterator_slot, [zero, zero])
    builder.store(zero, index_ptr)

    # Set length = -1 (sentinel value for streaming iterator)
    length_ptr = builder.gep(iterator_slot, [zero, one])
    builder.store(ir.Constant(i32, -1), length_ptr)

    # Set data_ptr = file_ptr_storage_typed (pointer to fake fat pointer containing FILE*)
    two = ir.Constant(i32, 2)
    data_ptr_ptr = builder.gep(iterator_slot, [zero, two])
    builder.store(file_ptr_storage_typed, data_ptr_ptr)

    # Load and return the iterator struct
    result = builder.load(iterator_slot)
    builder.ret(result)
