"""The conversions of a `u8[]` to a `string`: `to_string()` and `to_string_checked()`."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.string_layout import string_value
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I8
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend import gep_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

def emit_byte_array_to_string(codegen: "LLVMCodegen", receiver_value: ir.Value) -> ir.Value:
    """A fresh owned string with the bytes of the `u8[]` at the address `receiver_value`."""
    len_ptr = gep_utils.gep_dynamic_array_len(codegen, receiver_value, "")
    byte_count = codegen.builder.load(len_ptr)

    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, receiver_value, "")
    data_ptr = codegen.builder.load(data_ptr_ptr)

    # Allocate memory for string (byte_count + 1 for null terminator). emit_malloc
    # null-checks and traps RE2021 on failure.
    string_size = codegen.builder.add(byte_count, ir.Constant(codegen.types.i32, 1))
    string_size_i64 = codegen.builder.zext(string_size, ir.IntType(INT64_BIT_WIDTH))
    string_ptr = emit_malloc(codegen, codegen.builder, string_size_i64)

    # No UTF-8 validation: this is the zero-cost conversion, so invalid input is undefined
    # behaviour. `.to_string_checked()` is the validating twin.

    from sushi_lang.backend.expressions.memory import emit_memcpy_bytes
    emit_memcpy_bytes(codegen, string_ptr, data_ptr, byte_count)

    null_term_ptr = codegen.builder.gep(string_ptr, [byte_count])
    codegen.builder.store(ZERO_I8, null_term_ptr)

    return string_value(codegen.builder, string_ptr, byte_count, owned=1)


def emit_byte_array_to_string_checked(codegen: "LLVMCodegen",
                                      receiver_value: ir.Value) -> ir.Value:
    """`Result.Ok` of a fresh owned string when the bytes of the `u8[]` at the address
    `receiver_value` are valid UTF-8, else `Result.Err(StdError.Error)`."""
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    from sushi_lang.backend.types.arrays.methods.utf8_validate import get_or_emit_utf8_validate

    len_ptr = gep_utils.gep_dynamic_array_len(codegen, receiver_value, "")
    byte_count = codegen.builder.load(len_ptr)
    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, receiver_value, "")
    data_ptr = codegen.builder.load(data_ptr_ptr)

    validate_fn = get_or_emit_utf8_validate(codegen)
    is_valid = codegen.builder.call(validate_fn, [data_ptr, byte_count], name="utf8_valid")

    std_error = codegen.enum_table.by_name.get("StdError")
    result_type = ensure_result_type_in_table(codegen.enum_table, BuiltinType.STRING, std_error, struct_table=codegen.struct_table.by_name)
    result_llvm_type = codegen.types.ll_type(result_type)
    ok_index = result_type.get_variant_index("Ok")
    err_index = result_type.get_variant_index("Err")

    ok_bb = codegen.builder.append_basic_block("to_string_checked_ok")
    err_bb = codegen.builder.append_basic_block("to_string_checked_err")
    merge_bb = codegen.builder.append_basic_block("to_string_checked_merge")
    codegen.builder.cbranch(is_valid, ok_bb, err_bb)

    codegen.builder.position_at_end(ok_bb)
    string_value = emit_byte_array_to_string(codegen, receiver_value)
    ok_value = ir.Constant(result_llvm_type, ir.Undefined)
    ok_value = codegen.builder.insert_value(
        ok_value, ir.Constant(codegen.types.i32, ok_index), 0, name="Result_Ok_tag")
    data_array_type = result_llvm_type.elements[1]
    temp_alloca = entry_alloca(codegen.builder, data_array_type, name="ok_data_temp")
    typed_ptr = codegen.builder.bitcast(temp_alloca, ir.PointerType(string_value.type), name="ok_value_ptr")
    codegen.builder.store(string_value, typed_ptr)
    packed = codegen.builder.load(temp_alloca, name="ok_packed")
    ok_value = codegen.builder.insert_value(ok_value, packed, 1, name="Result_Ok_value")
    ok_end_bb = codegen.builder.block
    codegen.builder.branch(merge_bb)

    codegen.builder.position_at_end(err_bb)
    err_value = ir.Constant(result_llvm_type, ir.Undefined)
    err_value = codegen.builder.insert_value(
        err_value, ir.Constant(codegen.types.i32, err_index), 0, name="Result_Err_tag")
    codegen.builder.branch(merge_bb)

    codegen.builder.position_at_end(merge_bb)
    result_phi = codegen.builder.phi(result_llvm_type, name="to_string_checked_result")
    result_phi.add_incoming(ok_value, ok_end_bb)
    result_phi.add_incoming(err_value, err_bb)
    return result_phi

