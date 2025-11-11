"""
File open() function implementation with error handling.

This module contains the complex LLVM IR emission logic for the built-in
open(path, mode) function, including FileMode mapping and FileResult construction.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from backend.constants import INT32_BIT_WIDTH
from backend.llvm_constants import make_i32_const
from backend import enum_utils

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.ast import Call


def construct_file_result_ok(codegen: 'LLVMCodegen', file_ptr: ir.Value) -> ir.Value:
    """Construct FileResult.Ok(file) enum value.

    Args:
        codegen: The LLVM code generator.
        file_ptr: The file pointer (FILE*) to wrap.

    Returns:
        FileResult.Ok enum value containing the file pointer.
    """
    # Get FileResult enum type
    file_result_enum = codegen.enum_table.by_name["FileResult"]
    file_result_llvm_type = codegen.types.get_enum_type(file_result_enum)

    # Create FileResult.Ok enum value (tag 0 and contains a file handle)
    ok_enum = enum_utils.construct_enum_variant(
        codegen, file_result_llvm_type, variant_index=0, data=None, name_prefix="FileResult_Ok"
    )

    # Pack the file pointer into the data field
    # The data field is [N x i8] where N is large enough to hold a pointer (8 bytes on 64-bit)
    data_array_type = file_result_llvm_type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="ok_data_temp")
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="ok_data_ptr")

    # Store the file pointer at offset 0
    file_ptr_storage = codegen.builder.bitcast(
        data_ptr, ir.PointerType(codegen.types.str_ptr), name="file_ptr_storage"
    )
    codegen.builder.store(file_ptr, file_ptr_storage)

    # Load the packed data
    ok_data = codegen.builder.load(temp_alloca, name="ok_data")
    ok_enum = enum_utils.set_enum_data(codegen, ok_enum, ok_data, name="ok_enum")

    return ok_enum


def construct_file_error_from_errno(codegen: 'LLVMCodegen', errno_value: ir.Value) -> ir.Value:
    """Construct FileError enum value from errno.

    Args:
        codegen: The LLVM code generator.
        errno_value: The errno value to map to FileError variant.

    Returns:
        FileError enum value with appropriate variant tag.
    """
    # Map errno to FileError variant tag (0-8)
    file_error_tag = codegen.runtime.errors.map_errno_to_file_error(errno_value)

    # Get FileError enum type
    file_error_enum = codegen.enum_table.by_name["FileError"]
    file_error_llvm_type = codegen.types.get_enum_type(file_error_enum)

    # Create FileError enum value with the appropriate tag
    # FileError variants are all unit variants (no associated data)
    # Note: file_error_tag is runtime, so we can't use construct_enum_variant directly
    file_error_value = ir.Constant(file_error_llvm_type, ir.Undefined)
    file_error_value = codegen.builder.insert_value(
        file_error_value, file_error_tag, 0, name="file_error_tag"
    )

    # FileError data field should be zero (unit variants)
    file_error_data_type = file_error_llvm_type.elements[1]
    zero_file_error_data = ir.Constant(file_error_data_type, bytearray(file_error_data_type.count))
    file_error_value = codegen.builder.insert_value(
        file_error_value, zero_file_error_data, 1, name="file_error"
    )

    return file_error_value


def construct_file_result_err(codegen: 'LLVMCodegen', file_error: ir.Value) -> ir.Value:
    """Construct FileResult.Err(FileError) enum value.

    Args:
        codegen: The LLVM code generator.
        file_error: The FileError enum value to wrap.

    Returns:
        FileResult.Err enum value containing the FileError.
    """
    # Get FileResult enum type
    file_result_enum = codegen.enum_table.by_name["FileResult"]
    file_result_llvm_type = codegen.types.get_enum_type(file_result_enum)
    data_array_type = file_result_llvm_type.elements[1]

    # Create FileResult.Err enum value (tag 1) containing the FileError
    err_enum = enum_utils.construct_enum_variant(
        codegen, file_result_llvm_type, variant_index=1, data=None, name_prefix="FileResult_Err"
    )

    # Pack the FileError enum into the FileResult.Err data field
    # The data field is [N x i8] where N is large enough to hold the FileError enum
    temp_alloca = codegen.builder.alloca(data_array_type, name="err_data_temp")
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="err_data_ptr")

    # Store the FileError enum at offset 0
    file_error_llvm_type = file_error.type
    file_error_storage = codegen.builder.bitcast(
        data_ptr, ir.PointerType(file_error_llvm_type), name="file_error_storage"
    )
    codegen.builder.store(file_error, file_error_storage)

    # Load the packed data
    err_data = codegen.builder.load(temp_alloca, name="err_data")
    err_enum = enum_utils.set_enum_data(codegen, err_enum, err_data, name="err_enum")

    return err_enum


def emit_open_function(codegen: 'LLVMCodegen', expr: 'Call', to_i1: bool) -> ir.Value:
    """Emit open() built-in function call with FileMode mapping and error handling.

    Signature: open(string path, FileMode mode) FileResult
    Returns: FileResult enum (Ok(file) or Err())

    Args:
        codegen: The LLVM code generator.
        expr: The open() call expression.
        to_i1: Whether to convert result to i1 (should be False for enum).

    Returns:
        FileResult enum value: {i32 tag, [N x i8] data}
    """
    # Emit the path argument (first parameter) - this is a fat pointer {i8*, i32}
    path_fat_ptr = codegen.expressions.emit_expr(expr.args[0])

    # Convert fat pointer to null-terminated C string for fopen
    path_value = codegen.runtime.strings.emit_to_cstr(path_fat_ptr)

    # Emit the FileMode argument (second parameter) - this is an enum value
    mode_enum_value = codegen.expressions.emit_expr(expr.args[1])

    # Extract the FileMode variant tag from the enum struct
    # FileMode enum structure: {i32 tag, [N x i8] data}
    # For unit variants (no data), we only care about the tag
    mode_tag = enum_utils.extract_enum_tag(codegen, mode_enum_value, name="mode_tag")

    # Map FileMode variant tag to C fopen mode string
    # FileMode variants (from CollectorPass._register_predefined_enums):
    # 0: Read, 1: Write, 2: Append, 3: ReadB, 4: WriteB, 5: AppendB
    mode_strings = {
        0: "r",    # Read
        1: "w",    # Write
        2: "a",    # Append
        3: "rb",   # ReadB
        4: "wb",   # WriteB
        5: "ab"    # AppendB
    }

    # Create basic blocks for the mode switch and error handling
    # We need 6 blocks for each mode, plus error and success blocks
    blocks = {}
    for tag, mode_str in mode_strings.items():
        blocks[tag] = codegen.func.append_basic_block(f"mode_{mode_str}")

    fopen_call_block = codegen.func.append_basic_block("fopen_call")
    fopen_success_block = codegen.func.append_basic_block("fopen_success")
    fopen_error_block = codegen.func.append_basic_block("fopen_error")
    result_block = codegen.func.append_basic_block("open_result")

    # Switch on mode_tag to select the appropriate mode string
    switch = codegen.builder.switch(mode_tag, blocks[0])  # Default to "r"
    for tag in range(1, 6):
        switch.add_case(make_i32_const(tag), blocks[tag])

    # Create mode string literals for each case
    mode_ptrs = {}
    for tag, mode_str in mode_strings.items():
        codegen.builder.position_at_end(blocks[tag])
        mode_fat_ptr = codegen.runtime.strings.emit_string_literal(mode_str)
        # Extract data pointer (index 0) from fat pointer for C fopen
        mode_ptr = codegen.builder.extract_value(mode_fat_ptr, 0, name="mode_data")
        codegen.builder.branch(fopen_call_block)
        mode_ptrs[tag] = (mode_ptr, blocks[tag])

    # fopen_call block: Call fopen with path and mode
    codegen.builder.position_at_end(fopen_call_block)

    # Create phi node to merge mode_ptr from all mode blocks
    mode_phi = codegen.builder.phi(codegen.types.str_ptr, name="mode_phi")
    for tag, (mode_ptr, block) in mode_ptrs.items():
        mode_phi.add_incoming(mode_ptr, block)

    # Call fopen(path, mode)
    file_ptr = codegen.builder.call(codegen.runtime.libc_stdio.fopen, [path_value, mode_phi], name="file_ptr")

    # Check if fopen returned NULL (error)
    null_ptr = ir.Constant(codegen.types.str_ptr, None)
    is_null = codegen.builder.icmp_unsigned('==', file_ptr, null_ptr, name="is_null")

    # Branch based on NULL check
    codegen.builder.cbranch(is_null, fopen_error_block, fopen_success_block)

    # Success block: Return FileResult.Ok(file)
    codegen.builder.position_at_end(fopen_success_block)
    ok_enum = construct_file_result_ok(codegen, file_ptr)
    codegen.builder.branch(result_block)

    # Error block: Return FileResult.Err(FileError) with error details
    codegen.builder.position_at_end(fopen_error_block)

    # Get errno value and construct FileError enum
    errno_value = codegen.runtime.errors.get_errno()
    file_error_value = construct_file_error_from_errno(codegen, errno_value)

    # Wrap FileError in FileResult.Err
    err_enum = construct_file_result_err(codegen, file_error_value)
    codegen.builder.branch(result_block)

    # Result block: Phi node to merge Ok and Err results
    codegen.builder.position_at_end(result_block)
    file_result_enum = codegen.enum_table.by_name["FileResult"]
    file_result_llvm_type = codegen.types.get_enum_type(file_result_enum)
    result_phi = codegen.builder.phi(file_result_llvm_type, name="open_result")
    result_phi.add_incoming(ok_enum, fopen_success_block)
    result_phi.add_incoming(err_enum, fopen_error_block)

    return result_phi
