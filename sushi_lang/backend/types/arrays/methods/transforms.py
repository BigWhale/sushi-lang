"""Array cloning and type conversion operations."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I8, make_i32_const
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type

def emit_byte_array_to_string(codegen: "LLVMCodegen", call: MethodCall, receiver_value: ir.Value,
                               receiver_type: ir.LiteralStructType, _to_i1: bool) -> ir.Value:
    """Emit LLVM IR for u8[] to_string() method (convert byte array to UTF-8 string)."""
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="to_string", expected=0, got=len(call.args))

    zero = make_i32_const(0)

    len_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(0)])
    byte_count = codegen.builder.load(len_ptr)

    data_ptr_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(2)])
    data_ptr = codegen.builder.load(data_ptr_ptr)

    # Allocate memory for string (byte_count + 1 for null terminator). emit_malloc
    # null-checks and traps RE2021 on failure.
    string_size = codegen.builder.add(byte_count, ir.Constant(codegen.types.i32, 1))
    string_size_i64 = codegen.builder.zext(string_size, ir.IntType(INT64_BIT_WIDTH))
    string_ptr = emit_malloc(codegen, codegen.builder, string_size_i64)

    # PERFORMANCE: No UTF-8 validation for fast conversion
    # This method assumes byte arrays contain valid UTF-8 for zero-cost conversion.
    # Invalid UTF-8 sequences result in undefined behavior (similar to unsafe casts).
    #
    # FUTURE: A stdlib function bytes_to_string_checked() can provide validation
    # and return Result<string> for safety-critical code paths.
    #
    # Design rationale: Most real-world byte arrays (file I/O, network protocols)
    # contain valid UTF-8, so validation overhead is unnecessary in the common case.

    from sushi_lang.backend.statements.utils import emit_copy_loop
    emit_copy_loop(
        codegen=codegen,
        count=byte_count,
        src_ptr=data_ptr,
        dst_ptr=string_ptr,
        element_type=codegen.types.i8,
        name_prefix="to_string"
    )

    null_term_ptr = codegen.builder.gep(string_ptr, [byte_count])
    codegen.builder.store(ZERO_I8, null_term_ptr)

    string_struct_type = codegen.types.string_struct
    undef_struct = ir.Constant(string_struct_type, ir.Undefined)
    struct_with_data = codegen.builder.insert_value(undef_struct, string_ptr, 0)
    struct_with_size = codegen.builder.insert_value(struct_with_data, byte_count, 1)
    struct_complete = codegen.builder.insert_value(struct_with_size, ir.Constant(codegen.types.i8, 1), 2)

    return struct_complete


def emit_byte_array_to_string_checked(codegen: "LLVMCodegen", call: MethodCall, receiver_value: ir.Value,
                                      receiver_type: ir.LiteralStructType, _to_i1: bool) -> ir.Value:
    """Emit u8[] to_string_checked() -> Result<string, StdError>."""
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="to_string_checked", expected=0, got=len(call.args))

    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    from sushi_lang.backend.types.arrays.methods.utf8_validate import get_or_emit_utf8_validate

    zero = make_i32_const(0)
    len_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(0)])
    byte_count = codegen.builder.load(len_ptr)
    data_ptr_ptr = codegen.builder.gep(receiver_value, [zero, make_i32_const(2)])
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
    string_value = emit_byte_array_to_string(codegen, call, receiver_value, receiver_type, _to_i1)
    ok_value = ir.Constant(result_llvm_type, ir.Undefined)
    ok_value = codegen.builder.insert_value(
        ok_value, ir.Constant(codegen.types.i32, ok_index), 0, name="Result_Ok_tag")
    data_array_type = result_llvm_type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="ok_data_temp")
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


def emit_dynamic_array_clone(codegen: "LLVMCodegen", call: MethodCall, receiver_value: ir.Value,
                              receiver_type: ir.LiteralStructType, _to_i1: bool,
                              element_semantic_type: "Type") -> ir.Value:
    """Emit LLVM IR for dynamic array clone() method (deep copy)."""
    if len(call.args) != 0:
        raise_internal_error("CE0023", method="clone", expected=0, got=len(call.args))

    from sushi_lang.backend.expressions.memory import clone_dynamic_array_value

    source_array = codegen.builder.load(receiver_value, name="clone_source")
    cloned_array = clone_dynamic_array_value(codegen, source_array, element_semantic_type)

    clone_slot = codegen.builder.alloca(receiver_type, name="clone_slot")
    codegen.builder.store(cloned_array, clone_slot)

    return clone_slot

