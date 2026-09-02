"""List<T> debug method implementation."""

from typing import Any
from sushi_lang.semantics.typesys import StructType, Type, BuiltinType
from sushi_lang.semantics.generics.type_display import display_type
import llvmlite.ir as ir
from .types import extract_element_type, get_list_len_ptr, get_list_capacity_ptr, get_list_data_ptr
from sushi_lang.backend.constants.llvm_values import ZERO_I32, ONE_I32
from sushi_lang.backend.generics.debug_output import emit_debug_string, emit_debug_i32


def emit_list_debug(
    codegen: Any,
    list_value: ir.Value,
    list_type: StructType
) -> ir.Value:
    """Emit List<T>.debug() -> ~"""
    builder = codegen.builder

    element_type = extract_element_type(list_type, codegen)

    zero_i32 = ZERO_I32
    one_i32 = ONE_I32

    len_ptr = get_list_len_ptr(builder, list_value)
    capacity_ptr = get_list_capacity_ptr(builder, list_value)
    data_ptr_ptr = get_list_data_ptr(builder, list_value)

    length = builder.load(len_ptr, name="length")
    capacity = builder.load(capacity_ptr, name="capacity")
    data_ptr = builder.load(data_ptr_ptr, name="data_ptr")

    # Print header: "List@(T) {". The element type goes through display_type, not
    # str(): a monomorphized type's str() is its interned `<...>` identity name, so
    # interpolating it directly leaks the retired syntax for a nested generic
    # element ("List@(List<i32>)" instead of "List@(List@(i32))").
    header_str = f"List@({display_type(element_type)}) {{\n"
    emit_debug_string(codegen, builder, header_str)

    emit_debug_string(codegen, builder, "  len: ")
    emit_debug_i32(codegen, builder, length)
    emit_debug_string(codegen, builder, ", capacity: ")
    emit_debug_i32(codegen, builder, capacity)
    emit_debug_string(codegen, builder, "\n")

    i = builder.alloca(codegen.types.i32, name="i")
    builder.store(zero_i32, i)

    loop_cond_bb = builder.append_basic_block(name="debug_loop_cond")
    loop_body_bb = builder.append_basic_block(name="debug_loop_body")
    loop_end_bb = builder.append_basic_block(name="debug_loop_end")

    builder.branch(loop_cond_bb)

    builder.position_at_end(loop_cond_bb)
    i_val = builder.load(i, name="i_val")
    cond = builder.icmp_unsigned("<", i_val, length, name="loop_cond")
    builder.cbranch(cond, loop_body_bb, loop_end_bb)

    builder.position_at_end(loop_body_bb)
    i_val = builder.load(i, name="i_val")

    emit_debug_string(codegen, builder, "  [")
    emit_debug_i32(codegen, builder, i_val)
    emit_debug_string(codegen, builder, "] ")

    element_ptr = builder.gep(data_ptr, [i_val], name="element_ptr")
    element = builder.load(element_ptr, name="element")

    emit_debug_print_value(codegen, builder, element, element_type)
    emit_debug_string(codegen, builder, "\n")

    i_next = builder.add(i_val, one_i32, name="i_next")
    builder.store(i_next, i)
    builder.branch(loop_cond_bb)

    builder.position_at_end(loop_end_bb)

    emit_debug_string(codegen, builder, "}\n")

    return ir.Constant(codegen.types.i32, 0)


def emit_debug_print_value(codegen: Any, builder: Any, value: ir.Value, value_type: Type) -> None:
    """Helper to print a value for debug output."""

    if value_type == BuiltinType.I32:
        emit_debug_i32(codegen, builder, value)
    elif value_type == BuiltinType.I8:
        value_i32 = builder.zext(value, codegen.types.i32, name="i8_to_i32")
        emit_debug_i32(codegen, builder, value_i32)
    elif value_type == BuiltinType.I16:
        value_i32 = builder.sext(value, codegen.types.i32, name="i16_to_i32")
        emit_debug_i32(codegen, builder, value_i32)
    elif value_type == BuiltinType.I64:
        fmt_str = "%lld"
        str_bytes = (fmt_str + '\0').encode('utf-8')
        str_type = ir.ArrayType(ir.IntType(8), len(str_bytes))

        global_name = ".fmt_i64_debug"
        try:
            str_const = codegen.builder.module.get_global(global_name)
        except KeyError:
            str_const = ir.GlobalVariable(codegen.builder.module, str_type, name=global_name)
            str_const.linkage = 'internal'
            str_const.global_constant = True
            str_const.initializer = ir.Constant(str_type, bytearray(str_bytes))

            str_ptr = builder.gep(str_const, [ZERO_I32, ZERO_I32], name="fmt_ptr")

        printf_fn = codegen.runtime.libc_stdio.printf
        builder.call(printf_fn, [str_ptr, value])
    elif value_type == BuiltinType.STRING:
        emit_debug_string(codegen, builder, '"')
        fmt_str = "%s"
        str_bytes = (fmt_str + '\0').encode('utf-8')
        str_type = ir.ArrayType(ir.IntType(8), len(str_bytes))

        global_name = ".fmt_str_debug"
        try:
            str_const = codegen.builder.module.get_global(global_name)
        except KeyError:
            str_const = ir.GlobalVariable(codegen.builder.module, str_type, name=global_name)
            str_const.linkage = 'internal'
            str_const.global_constant = True
            str_const.initializer = ir.Constant(str_type, bytearray(str_bytes))

            str_ptr = builder.gep(str_const, [ZERO_I32, ZERO_I32], name="fmt_ptr")

        printf_fn = codegen.runtime.libc_stdio.printf
        builder.call(printf_fn, [str_ptr, value])
        emit_debug_string(codegen, builder, '"')
    elif value_type == BuiltinType.BOOL:
        true_bb = builder.append_basic_block(name="print_true")
        false_bb = builder.append_basic_block(name="print_false")
        after_bb = builder.append_basic_block(name="after_print_bool")

        is_true = builder.icmp_signed("!=", value, ir.Constant(codegen.types.i32, 0), name="is_true")
        builder.cbranch(is_true, true_bb, false_bb)

        builder.position_at_end(true_bb)
        emit_debug_string(codegen, builder, "true")
        builder.branch(after_bb)

        builder.position_at_end(false_bb)
        emit_debug_string(codegen, builder, "false")
        builder.branch(after_bb)

        builder.position_at_end(after_bb)
    elif value_type == BuiltinType.F32 or value_type == BuiltinType.F64:
        fmt_str = "%f"
        str_bytes = (fmt_str + '\0').encode('utf-8')
        str_type = ir.ArrayType(ir.IntType(8), len(str_bytes))

        global_name = ".fmt_float_debug"
        try:
            str_const = codegen.builder.module.get_global(global_name)
        except KeyError:
            str_const = ir.GlobalVariable(codegen.builder.module, str_type, name=global_name)
            str_const.linkage = 'internal'
            str_const.global_constant = True
            str_const.initializer = ir.Constant(str_type, bytearray(str_bytes))

            str_ptr = builder.gep(str_const, [ZERO_I32, ZERO_I32], name="fmt_ptr")

        if value_type == BuiltinType.F32:
            value = builder.fpext(value, ir.DoubleType(), name="f32_to_f64")

        printf_fn = codegen.runtime.libc_stdio.printf
        builder.call(printf_fn, [str_ptr, value])
    else:
        emit_debug_string(codegen, builder, "<value>")
