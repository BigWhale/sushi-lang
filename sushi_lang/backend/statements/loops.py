"""
Loop statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for loop-related statements including
foreach loops, break, and continue statements.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder, require_both_initialized

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Foreach


def emit_break(codegen: 'LLVMCodegen') -> None:
    """Emit break statement (jump to loop end).

    Creates an unconditional branch to the loop's end block and marks the
    current block as unreachable for subsequent code.

    Args:
        codegen: The main LLVMCodegen instance.

    Raises:
        AssertionError: If not inside a loop context.
    """
    assert codegen.loop_stack, "checker guarantees inside-loop"
    _, break_bb = codegen.loop_stack[-1]
    codegen.builder.branch(break_bb)
    codegen.utils.after_terminator_unreachable()


def emit_continue(codegen: 'LLVMCodegen') -> None:
    """Emit continue statement (jump to loop condition).

    Creates an unconditional branch to the loop's condition block and marks the
    current block as unreachable for subsequent code.

    Args:
        codegen: The main LLVMCodegen instance.

    Raises:
        AssertionError: If not inside a loop context.
    """
    assert codegen.loop_stack, "checker guarantees inside-loop"
    cont_bb, _ = codegen.loop_stack[-1]
    codegen.builder.branch(cont_bb)
    codegen.utils.after_terminator_unreachable()


def emit_foreach(codegen: 'LLVMCodegen', node: 'Foreach') -> None:
    """Emit foreach loop with iterator protocol.

    Desugars foreach into a while loop with iterator has_next/next operations:

    foreach(T item in iterable):
        body

    Becomes:

    let Iterator<T> __iter = iterable
    while (__iter.current_index < __iter.length):
        let T item = __iter.data_ptr[__iter.current_index]
        __iter.current_index = __iter.current_index + 1
        body

    For stdin.lines() iterators (length == -1), use special handling:
    while (true):
        let string line = stdin.readln()
        if (line.is_empty()): break
        body

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node to emit.
    """
    from llvmlite import ir
    from sushi_lang.semantics.typesys import IteratorType, BuiltinType

    builder, func = require_both_initialized(codegen)
    if node.item_type is None:
        raise_internal_error("CE0015", message="foreach item_type not resolved by semantic analysis")
    codegen.utils.ensure_open_block()

    # Check if this is a range expression (special case for zero-cost optimization)
    from sushi_lang.semantics.ast import RangeExpr
    if isinstance(node.iterable, RangeExpr):
        _emit_range_foreach(codegen, node, node.iterable)
        return

    # Emit the iterable expression to get the iterator
    iterator_value = codegen.expressions.emit_expr(node.iterable)
    iterator_type = IteratorType(element_type=node.item_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    # Allocate a slot for the iterator
    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="__iter")
    codegen.builder.store(iterator_value, iterator_slot)

    # Check if this is potentially a stdin.lines() iterator
    # stdin.lines() always returns strings, so we can check at compile-time
    is_string_iterator = (node.item_type == BuiltinType.STRING)

    zero = ir.Constant(codegen.types.i32, 0)

    # Check if this is a HashMap iterator by inspecting the iterable expression
    # HashMap.keys() and HashMap.values() are parsed as DotCall nodes
    from sushi_lang.semantics.ast import DotCall, Name
    from sushi_lang.semantics.typesys import StructType
    is_hashmap_keys_or_values = False
    hashmap_type = None
    hashmap_method = None

    # Check for HashMap.keys(), HashMap.values(), or HashMap.entries()
    if isinstance(node.iterable, DotCall):
        if node.iterable.method in ("keys", "values", "entries"):
            # The receiver should be a Name node referring to a HashMap variable
            if isinstance(node.iterable.receiver, Name):
                var_name = node.iterable.receiver.id
                # Look up the variable's semantic type using the memory manager
                receiver_type = codegen.memory.find_semantic_type(var_name)
                if isinstance(receiver_type, StructType) and receiver_type.name.startswith("HashMap<"):
                    is_hashmap_keys_or_values = True
                    hashmap_type = receiver_type
                    hashmap_method = node.iterable.method

    if is_hashmap_keys_or_values:
        # HashMap iterator path
        _emit_hashmap_foreach(codegen, node, iterator_slot, zero, hashmap_type, hashmap_method)
    elif is_string_iterator:
        # For string iterators, emit both stdin and array paths
        _emit_string_iterator_foreach(codegen, node, iterator_slot, zero)
    else:
        # For non-string iterators, emit only array path
        _emit_array_foreach(codegen, node, iterator_slot, zero)


def _emit_string_iterator_foreach(codegen: 'LLVMCodegen', node: 'Foreach', iterator_slot: 'ir.Value', zero: 'ir.Constant') -> None:
    """Emit foreach for string iterators (handles both stdin.lines() and array iterators).

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node.
        iterator_slot: The iterator slot allocation.
        zero: Constant zero for GEP operations.
    """
    from llvmlite import ir
    from sushi_lang.backend.statements import utils

    # Runtime check: is this a stdin/file iterator (length == -1)?
    length_ptr = utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    length = codegen.builder.load(length_ptr, name="length")
    is_stdin_iter = codegen.builder.icmp_signed("==", length, ir.Constant(codegen.types.i32, -1))

    # Create blocks for branching
    stdin_loop_bb = codegen.func.append_basic_block(name="foreach.stdin_loop")
    array_setup_bb = codegen.func.append_basic_block(name="foreach.array_setup")
    end_bb = codegen.func.append_basic_block(name="foreach.end")

    # Branch based on iterator type
    codegen.builder.cbranch(is_stdin_iter, stdin_loop_bb, array_setup_bb)

    # === stdin.lines() / file.lines() iterator path ===
    _emit_stdin_lines_foreach(codegen, node, iterator_slot, zero, stdin_loop_bb, end_bb)

    # === Array iterator path (for string arrays) ===
    codegen.builder.position_at_end(array_setup_bb)
    _emit_array_foreach_body(codegen, node, iterator_slot, zero, length_ptr, end_bb)


def _emit_stdin_lines_foreach(
    codegen: 'LLVMCodegen',
    node: 'Foreach',
    iterator_slot: 'ir.Value',
    zero: 'ir.Constant',
    stdin_loop_bb: 'ir.Block',
    end_bb: 'ir.Block'
) -> None:
    """Emit foreach for stdin.lines() or file.lines() iterators.

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node.
        iterator_slot: The iterator slot allocation.
        zero: Constant zero for GEP operations.
        stdin_loop_bb: The stdin loop entry block.
        end_bb: The loop end block.
    """
    from llvmlite import ir
    from sushi_lang.semantics.ast import MethodCall, Name
    from sushi_lang.sushi_stdlib.src.io.stdio.inline import _emit_readln as _emit_stdin_readln
    from sushi_lang.sushi_stdlib.src.io.files.inline import _emit_readln as _emit_file_readln
    from sushi_lang.sushi_stdlib.src.collections.strings_inline import emit_string_is_empty
    from sushi_lang.backend.statements import utils

    codegen.builder.position_at_end(stdin_loop_bb)
    stdin_cond_bb = codegen.func.append_basic_block(name="foreach.stdin_cond")
    stdin_body_bb = codegen.func.append_basic_block(name="foreach.stdin_body")

    codegen.builder.branch(stdin_cond_bb)

    # Condition: read a line and check if empty
    codegen.builder.position_at_end(stdin_cond_bb)

    # Get the FILE* from iterator's data_ptr field
    # For file.lines(), data_ptr stores a pointer to the FILE* pointer
    # For stdin.lines(), data_ptr is NULL
    data_ptr_ptr = utils.gep_struct_field(codegen, iterator_slot, 2, "file_ptr_ptr")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="file_ptr_ptr")

    # Check if data_ptr is NULL (indicates stdin.lines())
    null_ptr = ir.Constant(codegen.i8.as_pointer().as_pointer(), None)
    is_stdin = codegen.builder.icmp_unsigned('==', data_ptr, null_ptr)

    # Create blocks for stdin vs file
    use_stdin_bb = codegen.func.append_basic_block(name="foreach.use_stdin")
    use_file_bb = codegen.func.append_basic_block(name="foreach.use_file")
    check_empty_bb = codegen.func.append_basic_block(name="foreach.check_empty")

    codegen.builder.cbranch(is_stdin, use_stdin_bb, use_file_bb)

    # Use stdin.readln()
    codegen.builder.position_at_end(use_stdin_bb)
    # Create a dummy receiver Name node for stdin
    dummy_receiver = Name(id="stdin", loc=None)
    dummy_call = MethodCall(receiver=dummy_receiver, method="readln", args=[], loc=None)
    stdin_line_value = _emit_stdin_readln(codegen, dummy_call)
    stdin_exit_bb = codegen.builder.block
    codegen.builder.branch(check_empty_bb)

    # Use file.readln() on the FILE* pointer
    codegen.builder.position_at_end(use_file_bb)
    file_ptr = codegen.builder.load(data_ptr, name="file_ptr")
    # Create a dummy receiver Name node for file handle
    dummy_file_receiver = Name(id="__file_handle", loc=None)
    dummy_file_call = MethodCall(receiver=dummy_file_receiver, method="readln", args=[], loc=None)
    file_line_value = _emit_file_readln(codegen, dummy_file_call, file_ptr)
    file_exit_bb = codegen.builder.block
    codegen.builder.branch(check_empty_bb)

    # Merge the line values with a phi node
    # Lines are now fat pointers {i8*, i32}
    codegen.builder.position_at_end(check_empty_bb)
    from sushi_lang.semantics.typesys import BuiltinType
    string_struct_type = codegen.types.ll_type(BuiltinType.STRING)
    line_value = codegen.builder.phi(string_struct_type, name="line")
    line_value.add_incoming(stdin_line_value, stdin_exit_bb)
    line_value.add_incoming(file_line_value, file_exit_bb)

    # Check if line is empty (EOF)
    is_empty = emit_string_is_empty(codegen, line_value)

    # If not empty, continue; if empty, exit
    codegen.builder.cbranch(is_empty, end_bb, stdin_body_bb)

    # Body: use the line we just read
    codegen.builder.position_at_end(stdin_body_bb)
    codegen.loop_stack.append((stdin_cond_bb, end_bb))
    codegen.memory.push_scope()

    # Create slot for loop variable with the line we read
    element_ll_type = codegen.types.ll_type(node.item_type)
    codegen.memory.create_local(node.item_name, element_ll_type, line_value, node.item_type)

    # Emit the foreach body
    _emit_block(codegen, node.body)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    if codegen.builder.block.terminator is None:
        codegen.builder.branch(stdin_cond_bb)


def _emit_array_foreach(codegen: 'LLVMCodegen', node: 'Foreach', iterator_slot: 'ir.Value', zero: 'ir.Constant') -> None:
    """Emit foreach for regular array iterators (non-string types).

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node.
        iterator_slot: The iterator slot allocation.
        zero: Constant zero for GEP operations.
    """
    from llvmlite import ir
    from sushi_lang.backend.statements import utils

    # For non-string iterators, skip stdin path entirely
    end_bb = codegen.func.append_basic_block(name="foreach.end")
    length_ptr = utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    _emit_array_foreach_body(codegen, node, iterator_slot, zero, length_ptr, end_bb)


def _emit_array_foreach_body(
    codegen: 'LLVMCodegen',
    node: 'Foreach',
    iterator_slot: 'ir.Value',
    zero: 'ir.Constant',
    length_ptr: 'ir.Value',
    end_bb: 'ir.Block'
) -> None:
    """Emit the array iteration loop body.

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node.
        iterator_slot: The iterator slot allocation.
        zero: Constant zero for GEP operations.
        length_ptr: Pointer to the length field.
        end_bb: The loop end block.
    """
    from llvmlite import ir
    from sushi_lang.backend.statements import utils

    cond_bb = codegen.func.append_basic_block(name="foreach.cond")
    body_bb = codegen.func.append_basic_block(name="foreach.body")

    codegen.builder.branch(cond_bb)

    # Emit condition: current_index < length
    codegen.builder.position_at_end(cond_bb)

    # Get current_index (field 0)
    index_ptr = utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    current_index = codegen.builder.load(index_ptr, name="current_index")

    # Reload length (field 1)
    length = codegen.builder.load(length_ptr, name="length")

    # Compare: current_index < length
    has_next = codegen.builder.icmp_signed("<", current_index, length, name="has_next")
    codegen.builder.cbranch(has_next, body_bb, end_bb)

    # Emit body
    codegen.builder.position_at_end(body_bb)
    codegen.loop_stack.append((cond_bb, end_bb))
    codegen.memory.push_scope()

    # Get the current element: data_ptr[current_index]
    data_ptr_ptr = utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Index into the array: data_ptr[current_index]
    element_ptr = codegen.builder.gep(data_ptr, [current_index], name="element_ptr")
    element_value = codegen.builder.load(element_ptr, name=node.item_name)

    # Create slot for loop variable and store element
    element_ll_type = codegen.types.ll_type(node.item_type)
    codegen.memory.create_local(node.item_name, element_ll_type, element_value, node.item_type)

    # Increment current_index
    incremented_index = codegen.builder.add(current_index, ir.Constant(codegen.types.i32, 1), name="next_index")
    codegen.builder.store(incremented_index, index_ptr)

    # Emit the foreach body
    _emit_block(codegen, node.body)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    # Branch back to condition check
    if codegen.builder.block.terminator is None:
        codegen.builder.branch(cond_bb)

    # Position at end block (shared by both paths)
    codegen.builder.position_at_end(end_bb)


def _emit_hashmap_foreach(
    codegen: 'LLVMCodegen',
    node: 'Foreach',
    iterator_slot: 'ir.Value',
    zero: 'ir.Constant',
    hashmap_type: 'StructType',
    method: str
) -> None:
    """Emit foreach for HashMap.keys(), HashMap.values(), and HashMap.entries() iterators.

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node.
        iterator_slot: The iterator slot allocation.
        zero: Constant zero for GEP operations.
        hashmap_type: The HashMap<K, V> struct type.
        method: One of "keys", "values", or "entries".
    """
    from llvmlite import ir
    from sushi_lang.backend.statements import utils
    from sushi_lang.sushi_stdlib.generics.collections.hashmap.types import (
        extract_key_value_types, get_entry_type, get_user_entry_type,
        ensure_entry_type_in_struct_table, ENTRY_OCCUPIED,
    )

    # Extract K and V types from HashMap<K, V>
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    is_entries = (method == "entries")
    if is_entries:
        element_type = ensure_entry_type_in_struct_table(codegen.struct_table, key_type, value_type)
    else:
        element_type = key_type if method == "keys" else value_type
    entry_field_index = 0 if method == "keys" else 1  # 0=key, 1=value in Entry<K,V>

    # Get internal Entry<K,V> LLVM type (3-field: key, value, state)
    entry_type = get_entry_type(codegen, key_type, value_type)

    # Create basic blocks
    cond_bb = codegen.func.append_basic_block(name="foreach.hashmap_cond")
    check_occupied_bb = codegen.func.append_basic_block(name="foreach.hashmap_check_occupied")
    body_bb = codegen.func.append_basic_block(name="foreach.hashmap_body")
    increment_bb = codegen.func.append_basic_block(name="foreach.hashmap_increment")
    end_bb = codegen.func.append_basic_block(name="foreach.hashmap_end")

    codegen.builder.branch(cond_bb)

    # === Condition: current_index < capacity ===
    codegen.builder.position_at_end(cond_bb)

    index_ptr = utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    current_index = codegen.builder.load(index_ptr, name="current_index")

    capacity_ptr = utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    marked_capacity = codegen.builder.load(capacity_ptr, name="marked_capacity")

    # Extract actual capacity: marked_capacity & 0x1FFFFFFF (mask off bits 29-31)
    capacity_mask = ir.Constant(codegen.types.i32, 0x1FFFFFFF)
    actual_capacity = codegen.builder.and_(marked_capacity, capacity_mask, name="actual_capacity")

    # Check: current_index < actual_capacity
    has_more = codegen.builder.icmp_signed("<", current_index, actual_capacity, name="has_more")
    codegen.builder.cbranch(has_more, check_occupied_bb, end_bb)

    # === Check if current entry is Occupied ===
    codegen.builder.position_at_end(check_occupied_bb)

    # Get the data pointer (stored as element type pointer in iterator)
    data_ptr_ptr = utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="entries_ptr_as_element")

    # Cast to internal Entry<K,V,state>* to access the entry structure
    entry_ptr_type = ir.PointerType(entry_type)
    entries_ptr = codegen.builder.bitcast(data_ptr, entry_ptr_type, name="entries_ptr")

    # Get pointer to current entry: entries_ptr[current_index]
    current_entry_ptr = codegen.builder.gep(entries_ptr, [current_index], name="current_entry_ptr")

    # Access the state field (field 2) of internal Entry<K,V,state>
    state_ptr = utils.gep_struct_field(codegen, current_entry_ptr, 2, "state_ptr")
    state = codegen.builder.load(state_ptr, name="entry_state")

    # Check if state == ENTRY_OCCUPIED (1)
    is_occupied = codegen.builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")
    codegen.builder.cbranch(is_occupied, body_bb, increment_bb)

    # === Body: Extract element and execute foreach body ===
    codegen.builder.position_at_end(body_bb)
    codegen.loop_stack.append((cond_bb, end_bb))
    codegen.memory.push_scope()

    if is_entries:
        # Construct user-facing Entry<K, V> struct {key, value} from internal {key, value, state}
        user_entry_llvm = get_user_entry_type(codegen, key_type, value_type)

        key_ptr = utils.gep_struct_field(codegen, current_entry_ptr, 0, "entry_key_ptr")
        key_val = codegen.builder.load(key_ptr, name="entry_key")

        value_ptr = utils.gep_struct_field(codegen, current_entry_ptr, 1, "entry_value_ptr")
        value_val = codegen.builder.load(value_ptr, name="entry_value")

        # Build the 2-field struct
        entry_val = ir.Constant(user_entry_llvm, ir.Undefined)
        entry_val = codegen.builder.insert_value(entry_val, key_val, 0, name="entry_with_key")
        entry_val = codegen.builder.insert_value(entry_val, value_val, 1, name="entry_with_value")

        element_ll_type = user_entry_llvm
        codegen.memory.create_local(node.item_name, element_ll_type, entry_val, element_type)
        # Register type for field access (entry.key, entry.value)
        codegen.variable_types[node.item_name] = element_type
    else:
        # Extract the key (field 0) or value (field 1) from the current entry
        element_ptr = utils.gep_struct_field(codegen, current_entry_ptr, entry_field_index, "element_ptr")
        element_value = codegen.builder.load(element_ptr, name=node.item_name)

        element_ll_type = codegen.types.ll_type(element_type)
        codegen.memory.create_local(node.item_name, element_ll_type, element_value, element_type)

    # Emit the foreach body
    _emit_block(codegen, node.body)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    # Branch to increment
    if codegen.builder.block.terminator is None:
        codegen.builder.branch(increment_bb)

    # === Increment: current_index++ ===
    codegen.builder.position_at_end(increment_bb)
    incremented_index = codegen.builder.add(current_index, ir.Constant(codegen.types.i32, 1), name="next_index")
    codegen.builder.store(incremented_index, index_ptr)
    codegen.builder.branch(cond_bb)

    # Position at end block
    codegen.builder.position_at_end(end_bb)


def _emit_range_foreach(codegen: 'LLVMCodegen', node: 'Foreach', range_expr: 'RangeExpr') -> None:
    """Emit optimized foreach loop for range expressions.

    Compiles range expressions directly to for-loops without iterator overhead:

    foreach(i in 0..10):        # Exclusive
        body

    Becomes:
        start = 0
        end = 10
        if (start < end):
            # Ascending
            i = start
            while (i < end):
                body
                i = i + 1
        else:
            # Descending
            i = start
            while (i > end):
                body
                i = i - 1

    For inclusive ranges (..=), the condition adjusts to <= or >=.
    Empty ranges (start == end) produce zero iterations.

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node.
        range_expr: The range expression from node.iterable.
    """
    from llvmlite import ir

    builder, func = require_both_initialized(codegen)
    codegen.utils.ensure_open_block()

    # Emit start and end expressions (cast to i32)
    start_value = codegen.expressions.emit_expr(range_expr.start)
    start_i32 = codegen.utils.as_i32(start_value)

    end_value = codegen.expressions.emit_expr(range_expr.end)
    end_i32 = codegen.utils.as_i32(end_value)

    # Allocate slots for start/end/loop variable
    start_slot = codegen.builder.alloca(codegen.types.i32, name="range_start")
    codegen.builder.store(start_i32, start_slot)

    end_slot = codegen.builder.alloca(codegen.types.i32, name="range_end")
    codegen.builder.store(end_i32, end_slot)

    # Determine direction at runtime: start < end (ascending) vs start >= end (descending)
    start_loaded = codegen.builder.load(start_slot, name="start_val")
    end_loaded = codegen.builder.load(end_slot, name="end_val")
    is_ascending = codegen.builder.icmp_signed("<", start_loaded, end_loaded, name="is_ascending")

    # Create blocks
    ascending_bb = codegen.func.append_basic_block(name="range.ascending")
    descending_bb = codegen.func.append_basic_block(name="range.descending")
    end_bb = codegen.func.append_basic_block(name="range.end")

    # Branch based on direction
    codegen.builder.cbranch(is_ascending, ascending_bb, descending_bb)

    # === Ascending path ===
    codegen.builder.position_at_end(ascending_bb)
    _emit_range_loop_path(codegen, node, start_slot, end_slot, range_expr.inclusive, ascending=True, end_bb=end_bb)

    # === Descending path ===
    codegen.builder.position_at_end(descending_bb)
    _emit_range_loop_path(codegen, node, start_slot, end_slot, range_expr.inclusive, ascending=False, end_bb=end_bb)

    # Position at end block
    codegen.builder.position_at_end(end_bb)


def _emit_range_loop_path(
    codegen: 'LLVMCodegen',
    node: 'Foreach',
    start_slot: 'ir.Value',
    end_slot: 'ir.Value',
    inclusive: bool,
    ascending: bool,
    end_bb: 'ir.Block'
) -> None:
    """Emit one direction of the range loop (ascending or descending).

    Args:
        codegen: The main LLVMCodegen instance.
        node: The foreach statement node.
        start_slot: Allocated slot for start value.
        end_slot: Allocated slot for end value.
        inclusive: True for ..=, False for ..
        ascending: True for ascending loop, False for descending.
        end_bb: The shared end block.
    """
    from llvmlite import ir

    # Adjust end value for inclusive ranges
    end_val = codegen.builder.load(end_slot, name="end_val")
    if inclusive:
        if ascending:
            # Inclusive ascending: end = end + 1 (so i <= end becomes i < end+1)
            adjusted_end = codegen.builder.add(end_val, ir.Constant(codegen.types.i32, 1), name="adjusted_end")
        else:
            # Inclusive descending: end = end - 1 (so i >= end becomes i > end-1)
            adjusted_end = codegen.builder.sub(end_val, ir.Constant(codegen.types.i32, 1), name="adjusted_end")
    else:
        adjusted_end = end_val

    # Create loop blocks
    cond_bb = codegen.func.append_basic_block(name=f"range.{'asc' if ascending else 'desc'}.cond")
    body_bb = codegen.func.append_basic_block(name=f"range.{'asc' if ascending else 'desc'}.body")
    incr_bb = codegen.func.append_basic_block(name=f"range.{'asc' if ascending else 'desc'}.incr")

    # Initialize loop variable: i = start
    start_val = codegen.builder.load(start_slot, name="start_val")
    counter_slot = codegen.builder.alloca(codegen.types.i32, name=node.item_name)
    codegen.builder.store(start_val, counter_slot)

    codegen.builder.branch(cond_bb)

    # === Condition block ===
    codegen.builder.position_at_end(cond_bb)
    current_counter = codegen.builder.load(counter_slot, name=f"{node.item_name}_val")

    if ascending:
        # Ascending: i < adjusted_end
        condition = codegen.builder.icmp_signed("<", current_counter, adjusted_end, name="loop_cond")
    else:
        # Descending: i > adjusted_end
        condition = codegen.builder.icmp_signed(">", current_counter, adjusted_end, name="loop_cond")

    codegen.builder.cbranch(condition, body_bb, end_bb)

    # === Body block ===
    codegen.builder.position_at_end(body_bb)
    # Push increment block to loop stack so continue jumps there
    codegen.loop_stack.append((incr_bb, end_bb))
    codegen.memory.push_scope()

    # Register loop variable in scope
    element_ll_type = codegen.types.ll_type(node.item_type)
    counter_value = codegen.builder.load(counter_slot, name=node.item_name)
    codegen.memory.create_local(node.item_name, element_ll_type, counter_value, node.item_type)

    # Emit the foreach body
    _emit_block(codegen, node.body)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    # Branch to increment block if no terminator
    if codegen.builder.block.terminator is None:
        codegen.builder.branch(incr_bb)

    # === Increment block ===
    codegen.builder.position_at_end(incr_bb)
    current_val = codegen.builder.load(counter_slot, name="current_val")
    if ascending:
        next_val = codegen.builder.add(current_val, ir.Constant(codegen.types.i32, 1), name="next_val")
    else:
        next_val = codegen.builder.sub(current_val, ir.Constant(codegen.types.i32, 1), name="next_val")
    codegen.builder.store(next_val, counter_slot)
    codegen.builder.branch(cond_bb)


def _emit_block(codegen: 'LLVMCodegen', block) -> None:
    """Helper to emit a block of statements.

    Args:
        codegen: The main LLVMCodegen instance.
        block: The block AST node containing statements.
    """
    # Import here to avoid circular dependency
    from sushi_lang.backend.statements import StatementEmitter
    emitter = StatementEmitter(codegen)
    emitter.emit_block(block)
