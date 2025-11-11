"""
List<T> debug method implementation.

This module contains the debug method for printing List<T> internal state.
"""

from typing import Any
from semantics.typesys import StructType, Type, BuiltinType
import llvmlite.ir as ir
from .types import extract_element_type, get_list_len_ptr, get_list_capacity_ptr, get_list_data_ptr
from backend.llvm_constants import ZERO_I32, ONE_I32


def emit_list_debug(
    codegen: Any,
    list_value: ir.Value,
    list_type: StructType
) -> ir.Value:
    """Emit List<T>.debug() -> ~

    Prints the internal state of the List for debugging.

    Output format:
    List<T> {
      len: X, capacity: Y
      [0] element
      [1] element
      ...
    }

    Args:
        codegen: LLVM codegen instance.
        list_value: The List struct pointer.
        list_type: The List<T> struct type.

    Returns:
        Unit value (~).
    """
    builder = codegen.builder

    # Extract T type
    element_type = extract_element_type(list_type, codegen)

    # Constants (use shared definitions)
    zero_i32 = ZERO_I32
    one_i32 = ONE_I32

    # Get List fields
    len_ptr = get_list_len_ptr(builder, list_value)
    capacity_ptr = get_list_capacity_ptr(builder, list_value)
    data_ptr_ptr = get_list_data_ptr(builder, list_value)

    length = builder.load(len_ptr, name="length")
    capacity = builder.load(capacity_ptr, name="capacity")
    data_ptr = builder.load(data_ptr_ptr, name="data_ptr")

    # Print header: "List<T> {"
    header_str = f"List<{element_type}> {{\n"
    emit_printf_string(codegen, builder, header_str)

    # Print stats: "  len: X, capacity: Y"
    emit_printf_string(codegen, builder, "  len: ")
    emit_printf_i32(codegen, builder, length)
    emit_printf_string(codegen, builder, ", capacity: ")
    emit_printf_i32(codegen, builder, capacity)
    emit_printf_string(codegen, builder, "\n")

    # Iterate through elements and print each one
    i = builder.alloca(codegen.types.i32, name="i")
    builder.store(zero_i32, i)

    loop_cond_bb = builder.append_basic_block(name="debug_loop_cond")
    loop_body_bb = builder.append_basic_block(name="debug_loop_body")
    loop_end_bb = builder.append_basic_block(name="debug_loop_end")

    builder.branch(loop_cond_bb)

    # Loop condition: i < len
    builder.position_at_end(loop_cond_bb)
    i_val = builder.load(i, name="i_val")
    cond = builder.icmp_unsigned("<", i_val, length, name="loop_cond")
    builder.cbranch(cond, loop_body_bb, loop_end_bb)

    # Loop body: print element
    builder.position_at_end(loop_body_bb)
    i_val = builder.load(i, name="i_val")

    # Print index: "  [i] "
    emit_printf_string(codegen, builder, "  [")
    emit_printf_i32(codegen, builder, i_val)
    emit_printf_string(codegen, builder, "] ")

    # Get element at index i
    element_ptr = builder.gep(data_ptr, [i_val], name="element_ptr")
    element = builder.load(element_ptr, name="element")

    # Print element value
    emit_debug_print_value(codegen, builder, element, element_type)
    emit_printf_string(codegen, builder, "\n")

    # Increment i and continue loop
    i_next = builder.add(i_val, one_i32, name="i_next")
    builder.store(i_next, i)
    builder.branch(loop_cond_bb)

    # End loop
    builder.position_at_end(loop_end_bb)

    # Print closing brace
    emit_printf_string(codegen, builder, "}\n")

    # Return unit (~)
    return ir.Constant(codegen.types.i32, 0)


def emit_printf_string(codegen: Any, builder: Any, text: str) -> None:
    """Helper to print a string using printf.

    Args:
        codegen: LLVM codegen instance.
        builder: LLVM builder.
        text: The string to print (can be a Python string or IR value).
    """
    # Create a global string constant
    str_bytes = (text + '\0').encode('utf-8')
    str_type = ir.ArrayType(ir.IntType(8), len(str_bytes))

    # Generate unique name
    global_name = f".str_debug_{abs(hash(text)) % 1000000}"

    # Try to get existing global or create new one
    try:
        str_const = codegen.builder.module.get_global(global_name)
    except KeyError:
        str_const = ir.GlobalVariable(codegen.builder.module, str_type, name=global_name)
        str_const.linkage = 'internal'
        str_const.global_constant = True
        str_const.initializer = ir.Constant(str_type, bytearray(str_bytes))

    # Get pointer to string
    str_ptr = builder.gep(str_const, [ZERO_I32, ZERO_I32], name="str_ptr")

    # Call printf
    printf_fn = codegen.runtime.libc_stdio.printf
    builder.call(printf_fn, [str_ptr])


def emit_printf_i32(codegen: Any, builder: Any, value: ir.Value) -> None:
    """Helper to print an i32 using printf.

    Args:
        codegen: LLVM codegen instance.
        builder: LLVM builder.
        value: The i32 value to print.
    """
    fmt_str = "%d"
    str_bytes = (fmt_str + '\0').encode('utf-8')
    str_type = ir.ArrayType(ir.IntType(8), len(str_bytes))

    # Try to get existing global or create new one
    global_name = ".fmt_i32_debug"
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


def emit_debug_print_value(codegen: Any, builder: Any, value: ir.Value, value_type: Type) -> None:
    """Helper to print a value for debug output.

    Args:
        codegen: LLVM codegen instance.
        builder: LLVM builder.
        value: The value to print.
        value_type: The semantic type of the value.
    """
    from semantics.typesys import BuiltinType

    # Print based on type
    if value_type == BuiltinType.I32:
        emit_printf_i32(codegen, builder, value)
    elif value_type == BuiltinType.I8:
        # Print i8 as i32
        value_i32 = builder.zext(value, codegen.types.i32, name="i8_to_i32")
        emit_printf_i32(codegen, builder, value_i32)
    elif value_type == BuiltinType.I16:
        # Print i16 as i32
        value_i32 = builder.sext(value, codegen.types.i32, name="i16_to_i32")
        emit_printf_i32(codegen, builder, value_i32)
    elif value_type == BuiltinType.I64:
        # Print i64 using %lld
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
        emit_printf_string(codegen, builder, '"')
        # Print the string value
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
        emit_printf_string(codegen, builder, '"')
    elif value_type == BuiltinType.BOOL:
        # Convert bool (i32) to string
        true_bb = builder.append_basic_block(name="print_true")
        false_bb = builder.append_basic_block(name="print_false")
        after_bb = builder.append_basic_block(name="after_print_bool")

        is_true = builder.icmp_signed("!=", value, ir.Constant(codegen.types.i32, 0), name="is_true")
        builder.cbranch(is_true, true_bb, false_bb)

        builder.position_at_end(true_bb)
        emit_printf_string(codegen, builder, "true")
        builder.branch(after_bb)

        builder.position_at_end(false_bb)
        emit_printf_string(codegen, builder, "false")
        builder.branch(after_bb)

        builder.position_at_end(after_bb)
    elif value_type == BuiltinType.F32 or value_type == BuiltinType.F64:
        # Print float/double using %f
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

        # Convert f32 to f64 for printf
        if value_type == BuiltinType.F32:
            value = builder.fpext(value, ir.DoubleType(), name="f32_to_f64")

        printf_fn = codegen.runtime.libc_stdio.printf
        builder.call(printf_fn, [str_ptr, value])
    else:
        # For other types, just print a placeholder
        emit_printf_string(codegen, builder, "<value>")
