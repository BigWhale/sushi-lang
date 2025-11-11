"""
HashMap<K, V> debug method implementation.

This module contains the debug method for printing HashMap internal state.
"""

from typing import Any
from semantics.ast import MethodCall
from semantics.typesys import StructType, Type, BuiltinType
import llvmlite.ir as ir
from ..types import get_entry_type, extract_key_value_types, ENTRY_EMPTY, ENTRY_OCCUPIED, ENTRY_TOMBSTONE
from backend.llvm_constants import ZERO_I32, make_i32_const, make_i8_const


def emit_hashmap_debug(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.debug() -> ~

    Prints the internal state of the HashMap for debugging.

    Output format:
    HashMap<K, V> {
      size: X, capacity: Y, tombstones: Z
      [0] Empty
      [1] Occupied: key -> value
      [2] Tombstone
      ...
    }

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Unit value (~).
    """
    builder = codegen.builder

    # Extract K and V types
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get LLVM types
    entry_type = get_entry_type(codegen, key_type, value_type)

    # Constants
    zero_i32 = ZERO_I32
    one_i32 = make_i32_const(1)

    # Get HashMap fields
    size_ptr = builder.gep(hashmap_value, [zero_i32, one_i32], name="size_ptr")
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, make_i32_const(2)], name="capacity_ptr")
    tombstones_ptr = builder.gep(hashmap_value, [zero_i32, make_i32_const(3)], name="tombstones_ptr")

    size = builder.load(size_ptr, name="size")
    capacity = builder.load(capacity_ptr, name="capacity")
    tombstones = builder.load(tombstones_ptr, name="tombstones")

    # Get buckets pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, make_i32_const(2)], name="buckets_data_ptr")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    # Print header: "HashMap<K, V> {"
    header_str = f"HashMap<{key_type}, {value_type}> {{\n"
    emit_printf_string(codegen, builder, header_str)

    # Print stats: "  size: X, capacity: Y, tombstones: Z"
    emit_printf_string(codegen, builder, "  size: ")
    emit_printf_i32(codegen, builder, size)
    emit_printf_string(codegen, builder, "\n")

    emit_printf_string(codegen, builder, "  capacity: ")
    emit_printf_i32(codegen, builder, capacity)
    emit_printf_string(codegen, builder, "\n")

    emit_printf_string(codegen, builder, "  tombstones: ")
    emit_printf_i32(codegen, builder, tombstones)
    emit_printf_string(codegen, builder, "\n")

    # Iterate through buckets and print each entry
    i = builder.alloca(codegen.types.i32, name="i")
    builder.store(zero_i32, i)

    loop_cond_bb = builder.append_basic_block(name="debug_loop_cond")
    loop_body_bb = builder.append_basic_block(name="debug_loop_body")
    check_state_bb = builder.append_basic_block(name="debug_check_state")
    empty_bb = builder.append_basic_block(name="debug_empty")
    occupied_bb = builder.append_basic_block(name="debug_occupied")
    tombstone_bb = builder.append_basic_block(name="debug_tombstone")
    loop_continue_bb = builder.append_basic_block(name="debug_loop_continue")
    loop_end_bb = builder.append_basic_block(name="debug_loop_end")

    builder.branch(loop_cond_bb)

    # Loop condition: i < capacity
    builder.position_at_end(loop_cond_bb)
    i_val = builder.load(i, name="i_val")
    cond = builder.icmp_unsigned("<", i_val, capacity, name="loop_cond")
    builder.cbranch(cond, loop_body_bb, loop_end_bb)

    # Loop body: print entry
    builder.position_at_end(loop_body_bb)
    i_val = builder.load(i, name="i_val")

    # Get entry
    entry_ptr = builder.gep(buckets_data, [i_val], name="entry_ptr")
    state_ptr = builder.gep(entry_ptr, [zero_i32, make_i32_const(2)], name="state_ptr")
    state = builder.load(state_ptr, name="state")

    builder.branch(check_state_bb)

    # Check state
    builder.position_at_end(check_state_bb)
    is_empty = builder.icmp_unsigned("==", state, make_i8_const(ENTRY_EMPTY), name="is_empty")
    is_occupied = builder.icmp_unsigned("==", state, make_i8_const(ENTRY_OCCUPIED), name="is_occupied")
    is_tombstone = builder.icmp_unsigned("==", state, make_i8_const(ENTRY_TOMBSTONE), name="is_tombstone")

    # Branch: if empty
    check_occupied_bb = builder.append_basic_block(name="check_occupied")
    builder.cbranch(is_empty, empty_bb, check_occupied_bb)

    # Check if occupied
    builder.position_at_end(check_occupied_bb)
    check_tombstone_bb = builder.append_basic_block(name="check_tombstone")
    builder.cbranch(is_occupied, occupied_bb, check_tombstone_bb)

    # Check if tombstone
    builder.position_at_end(check_tombstone_bb)
    builder.cbranch(is_tombstone, tombstone_bb, loop_continue_bb)

    # Empty case
    builder.position_at_end(empty_bb)
    emit_printf_string(codegen, builder, "  [")
    emit_printf_i32(codegen, builder, i_val)
    emit_printf_string(codegen, builder, "] Empty\n")
    builder.branch(loop_continue_bb)

    # Occupied case - print key and value
    builder.position_at_end(occupied_bb)
    emit_printf_string(codegen, builder, "  [")
    emit_printf_i32(codegen, builder, i_val)
    emit_printf_string(codegen, builder, "] Occupied: ")

    # Load and print key
    key_ptr = builder.gep(entry_ptr, [zero_i32, zero_i32], name="key_ptr")
    key = builder.load(key_ptr, name="key")
    emit_debug_print_value(codegen, builder, key, key_type)

    emit_printf_string(codegen, builder, " -> ")

    # Load and print value
    value_ptr = builder.gep(entry_ptr, [zero_i32, one_i32], name="value_ptr")
    value = builder.load(value_ptr, name="value")
    emit_debug_print_value(codegen, builder, value, value_type)

    emit_printf_string(codegen, builder, "\n")
    builder.branch(loop_continue_bb)

    # Tombstone case
    builder.position_at_end(tombstone_bb)
    emit_printf_string(codegen, builder, "  [")
    emit_printf_i32(codegen, builder, i_val)
    emit_printf_string(codegen, builder, "] Tombstone\n")
    builder.branch(loop_continue_bb)

    # Continue loop
    builder.position_at_end(loop_continue_bb)
    i_val = builder.load(i, name="i_val")
    i_next = builder.add(i_val, one_i32, name="i_next")
    builder.store(i_next, i)
    builder.branch(loop_cond_bb)

    # End loop
    builder.position_at_end(loop_end_bb)

    # Print closing brace
    emit_printf_string(codegen, builder, "}\n")

    # Return unit (~)
    return ZERO_I32


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
    zero = ZERO_I32
    str_ptr = builder.gep(str_const, [zero, zero], name="str_ptr")

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

    zero = ZERO_I32
    str_ptr = builder.gep(str_const, [zero, zero], name="fmt_ptr")

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

        zero = ZERO_I32
        str_ptr = builder.gep(str_const, [zero, zero], name="fmt_ptr")

        printf_fn = codegen.runtime.libc_stdio.printf
        builder.call(printf_fn, [str_ptr, value])
        emit_printf_string(codegen, builder, '"')
    elif value_type == BuiltinType.BOOL:
        # Convert bool (i32) to string
        true_bb = builder.append_basic_block(name="print_true")
        false_bb = builder.append_basic_block(name="print_false")
        after_bb = builder.append_basic_block(name="after_print_bool")

        is_true = builder.icmp_signed("!=", value, ZERO_I32, name="is_true")
        builder.cbranch(is_true, true_bb, false_bb)

        builder.position_at_end(true_bb)
        emit_printf_string(codegen, builder, "true")
        builder.branch(after_bb)

        builder.position_at_end(false_bb)
        emit_printf_string(codegen, builder, "false")
        builder.branch(after_bb)

        builder.position_at_end(after_bb)
    else:
        # For other types, just print a placeholder
        emit_printf_string(codegen, builder, "<value>")


