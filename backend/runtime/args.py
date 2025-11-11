"""
Command-line argument handling for main() wrapper generation.

This module provides helper functions for converting C-style argc/argv
to Sushi's string[] dynamic array in the main() wrapper.
"""
from typing import TYPE_CHECKING
import llvmlite.ir as ir
from semantics.typesys import DynamicArrayType, BuiltinType

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def allocate_string_array_data(codegen: 'LLVMCodegen', count: ir.Value) -> ir.Value:
    """Allocate memory for a string[] dynamic array data buffer.

    This handles the complex alignment calculation for fat pointer strings.
    Strings are {i8*, i32} with size 12 bytes but alignment 8 bytes,
    resulting in stride of 16 bytes.

    Args:
        codegen: LLVM code generator instance
        count: Number of string elements (i32)

    Returns:
        Typed pointer to allocated string struct array
    """
    # Strings are fat pointers: {i8*, i32}
    string_struct_type = codegen.types.ll_type(BuiltinType.STRING)

    # Calculate element size with proper alignment
    # Formula: aligned_size = ((size + align - 1) / align) * align
    # Size is 12 bytes, alignment is 8 bytes, so stride is 16 bytes
    size_bytes = codegen.types.get_type_size_bytes(BuiltinType.STRING)  # 12
    alignment = codegen.types._get_type_alignment(BuiltinType.STRING)   # 8
    element_size = ((size_bytes + alignment - 1) // alignment) * alignment  # 16

    # Calculate total bytes needed
    total_bytes = codegen.builder.mul(count, ir.Constant(codegen.i32, element_size), name="total_bytes")

    # Convert to 64-bit for malloc
    total_bytes_64 = codegen.builder.zext(total_bytes, ir.IntType(64), name="total_bytes_64")

    # Allocate memory
    malloc_func = codegen.get_malloc_func()
    data_ptr = codegen.builder.call(malloc_func, [total_bytes_64], name="string_array_data")

    # Cast void* to string struct array pointer
    string_struct_ptr_type = ir.PointerType(string_struct_type)
    return codegen.builder.bitcast(data_ptr, string_struct_ptr_type, name="typed_string_array")


def populate_string_array_from_argv(
    codegen: 'LLVMCodegen',
    argc: ir.Value,
    argv: ir.Value,
    target_array_data: ir.Value
) -> None:
    """Convert C argv array to fat pointer strings in target array.

    Iterates through argv[0..argc-1], converting each char* to a fat pointer
    {i8*, i32} using strlen, and storing in the target array.

    Args:
        codegen: LLVM code generator instance
        argc: Argument count (i32)
        argv: C-style argument vector (char**)
        target_array_data: Pointer to string struct array to populate
    """
    builder = codegen.builder
    zero_i32 = ir.Constant(codegen.i32, 0)
    one_i32 = ir.Constant(codegen.i32, 1)

    # Get strlen function
    strlen_func = codegen.runtime.libc_strings.strlen

    # Create loop blocks
    loop_cond = builder.append_basic_block(name="argv_loop_cond")
    loop_body = builder.append_basic_block(name="argv_loop_body")
    loop_end = builder.append_basic_block(name="argv_loop_end")

    # Initialize loop counter
    counter = builder.alloca(codegen.i32, name="argv_counter")
    builder.store(zero_i32, counter)
    builder.branch(loop_cond)

    # Loop condition: counter < argc
    builder.position_at_end(loop_cond)
    counter_val = builder.load(counter, name="counter")
    cmp = builder.icmp_signed("<", counter_val, argc, name="loop_cond")
    builder.cbranch(cmp, loop_body, loop_end)

    # Loop body: convert argv[i] to fat pointer
    builder.position_at_end(loop_body)
    counter_val = builder.load(counter, name="counter")

    # Get argv[i] (char* from C)
    argv_i_ptr = builder.gep(argv, [counter_val], name="argv_i_ptr")
    argv_i = builder.load(argv_i_ptr, name="argv_i")

    # Call strlen to get length
    strlen_result = builder.call(strlen_func, [argv_i], name="strlen_result")

    # Get pointer to string struct slot in array
    string_slot = builder.gep(target_array_data, [counter_val], name="string_slot")

    # Store fat pointer: {ptr, len}
    ptr_field = builder.gep(string_slot, [zero_i32, zero_i32], name="ptr_field")
    len_field = builder.gep(string_slot, [zero_i32, one_i32], name="len_field")
    builder.store(argv_i, ptr_field)
    builder.store(strlen_result, len_field)

    # Increment counter
    next_counter = builder.add(counter_val, one_i32, name="next_counter")
    builder.store(next_counter, counter)
    builder.branch(loop_cond)

    # Loop end
    builder.position_at_end(loop_end)


def generate_argc_argv_conversion(codegen: 'LLVMCodegen', argc: ir.Value, argv: ir.Value) -> ir.Value:
    """Convert C-style argc/argv to Sushi string[] dynamic array.

    This is the main entry point for command-line argument conversion in the
    main() wrapper. It creates a string[] with the same length and capacity,
    populated with fat pointers converted from C strings.

    Args:
        codegen: LLVM code generator instance
        argc: Argument count (i32)
        argv: Argument vector (char**)

    Returns:
        Pointer to string[] dynamic array struct (alloca)
    """
    builder = codegen.builder
    zero_i32 = ir.Constant(codegen.i32, 0)
    one_i32 = ir.Constant(codegen.i32, 1)
    two_i32 = ir.Constant(codegen.i32, 2)

    # Create dynamic array type for strings
    string_array_type = DynamicArrayType(BuiltinType.STRING)

    # Declare the dynamic array and get its struct allocation
    args_array_alloca = codegen.dynamic_arrays.declare_dynamic_array("cmd_args", string_array_type)

    # Allocate data buffer for strings with proper alignment
    typed_data_ptr = allocate_string_array_data(codegen, argc)

    # Update array struct fields: {len, cap, data}
    len_ptr = builder.gep(args_array_alloca, [zero_i32, zero_i32], name="len_ptr")
    cap_ptr = builder.gep(args_array_alloca, [zero_i32, one_i32], name="cap_ptr")
    data_ptr_ptr = builder.gep(args_array_alloca, [zero_i32, two_i32], name="data_ptr_ptr")

    builder.store(argc, len_ptr)           # length = argc
    builder.store(argc, cap_ptr)           # capacity = argc
    builder.store(typed_data_ptr, data_ptr_ptr)  # data pointer

    # Convert each argv[i] to fat pointer and populate array
    populate_string_array_from_argv(codegen, argc, argv, typed_data_ptr)

    return args_array_alloca
