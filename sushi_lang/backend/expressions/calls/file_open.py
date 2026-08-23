"""File open() function implementation with error handling."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants.llvm_values import make_i32_const
from sushi_lang.backend import enum_utils
from sushi_lang.backend.expressions.calls.utils import emit_cstr_arg

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Call


def construct_file_result_ok(codegen: 'LLVMCodegen', file_ptr: ir.Value) -> ir.Value:
    """Construct FileResult.Ok(file) enum value."""
    file_result_enum = codegen.enum_table.by_name["FileResult"]
    file_result_llvm_type = codegen.types.get_enum_type(file_result_enum)

    ok_enum = enum_utils.construct_enum_variant(
        codegen, file_result_llvm_type, variant_index=0, data=None, name_prefix="FileResult_Ok"
    )

    data_array_type = file_result_llvm_type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="ok_data_temp")
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="ok_data_ptr")

    file_ptr_storage = codegen.builder.bitcast(
        data_ptr, ir.PointerType(codegen.types.str_ptr), name="file_ptr_storage"
    )
    codegen.builder.store(file_ptr, file_ptr_storage)

    ok_data = codegen.builder.load(temp_alloca, name="ok_data")
    ok_enum = enum_utils.set_enum_data(codegen, ok_enum, ok_data, name="ok_enum")

    return ok_enum


def construct_file_error_from_errno(codegen: 'LLVMCodegen', errno_value: ir.Value) -> ir.Value:
    """Construct FileError enum value from errno."""
    file_error_tag = codegen.runtime.errors.map_errno_to_file_error(errno_value)

    file_error_enum = codegen.enum_table.by_name["FileError"]
    file_error_llvm_type = codegen.types.get_enum_type(file_error_enum)

    # `file_error_tag` is a runtime value, so construct_enum_variant cannot be used.
    # Every FileError variant is a unit variant, so only the tag is stored.
    file_error_value = ir.Constant(file_error_llvm_type, ir.Undefined)
    file_error_value = codegen.builder.insert_value(
        file_error_value, file_error_tag, 0, name="file_error_tag"
    )

    # FileError data field should be zero (unit variants). Use zeroinitializer (None)
    # so the constant's element type always matches the data array ([1 x i64] since
    # #300 phase 2; bytearray would build i8 elements and fail IR parsing).
    file_error_data_type = file_error_llvm_type.elements[1]
    zero_file_error_data = ir.Constant(file_error_data_type, None)
    file_error_value = codegen.builder.insert_value(
        file_error_value, zero_file_error_data, 1, name="file_error"
    )

    return file_error_value


def construct_file_result_err(codegen: 'LLVMCodegen', file_error: ir.Value) -> ir.Value:
    """Construct FileResult.Err(FileError) enum value."""
    file_result_enum = codegen.enum_table.by_name["FileResult"]
    file_result_llvm_type = codegen.types.get_enum_type(file_result_enum)
    data_array_type = file_result_llvm_type.elements[1]

    err_enum = enum_utils.construct_enum_variant(
        codegen, file_result_llvm_type, variant_index=1, data=None, name_prefix="FileResult_Err"
    )

    # ENTRY block: an open() inside a loop must not grow the frame (BUGS.md B1).
    temp_alloca = codegen.memory.entry_alloca(data_array_type, "err_data_temp")
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="err_data_ptr")

    file_error_llvm_type = file_error.type
    file_error_storage = codegen.builder.bitcast(
        data_ptr, ir.PointerType(file_error_llvm_type), name="file_error_storage"
    )
    codegen.builder.store(file_error, file_error_storage)

    err_data = codegen.builder.load(temp_alloca, name="err_data")
    err_enum = enum_utils.set_enum_data(codegen, err_enum, err_data, name="err_enum")

    return err_enum


def emit_open_function(codegen: 'LLVMCodegen', expr: 'Call', to_i1: bool) -> ir.Value:
    """Emit open() built-in function call with FileMode mapping and error handling."""
    # Path C of #292: this marshalled without registering the copy, so `open()` leaked one
    # block per call while the FFI arm two files away did the same marshal and registered it.
    path_value = emit_cstr_arg(codegen, expr.args[0])

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

    blocks = {}
    for tag, mode_str in mode_strings.items():
        blocks[tag] = codegen.func.append_basic_block(f"mode_{mode_str}")

    fopen_call_block = codegen.func.append_basic_block("fopen_call")
    fopen_success_block = codegen.func.append_basic_block("fopen_success")
    fopen_error_block = codegen.func.append_basic_block("fopen_error")
    result_block = codegen.func.append_basic_block("open_result")

    switch = codegen.builder.switch(mode_tag, blocks[0])  # Default to "r"
    for tag in range(1, 6):
        switch.add_case(make_i32_const(tag), blocks[tag])

    mode_ptrs = {}
    for tag, mode_str in mode_strings.items():
        codegen.builder.position_at_end(blocks[tag])
        mode_fat_ptr = codegen.runtime.strings.emit_string_literal(mode_str)
        mode_ptr = codegen.builder.extract_value(mode_fat_ptr, 0, name="mode_data")
        codegen.builder.branch(fopen_call_block)
        mode_ptrs[tag] = (mode_ptr, blocks[tag])

    codegen.builder.position_at_end(fopen_call_block)

    mode_phi = codegen.builder.phi(codegen.types.str_ptr, name="mode_phi")
    for _tag, (mode_ptr, block) in mode_ptrs.items():
        mode_phi.add_incoming(mode_ptr, block)

    file_ptr = codegen.builder.call(codegen.runtime.libc_stdio.fopen, [path_value, mode_phi], name="file_ptr")

    null_ptr = ir.Constant(codegen.types.str_ptr, None)
    is_null = codegen.builder.icmp_unsigned('==', file_ptr, null_ptr, name="is_null")

    codegen.builder.cbranch(is_null, fopen_error_block, fopen_success_block)

    codegen.builder.position_at_end(fopen_success_block)
    ok_enum = construct_file_result_ok(codegen, file_ptr)
    codegen.builder.branch(result_block)

    codegen.builder.position_at_end(fopen_error_block)

    errno_value = codegen.runtime.errors.get_errno()
    file_error_value = construct_file_error_from_errno(codegen, errno_value)

    err_enum = construct_file_result_err(codegen, file_error_value)
    codegen.builder.branch(result_block)

    codegen.builder.position_at_end(result_block)
    file_result_enum = codegen.enum_table.by_name["FileResult"]
    file_result_llvm_type = codegen.types.get_enum_type(file_result_enum)
    result_phi = codegen.builder.phi(file_result_llvm_type, name="open_result")
    result_phi.add_incoming(ok_enum, fopen_success_block)
    result_phi.add_incoming(err_enum, fopen_error_block)

    return result_phi
