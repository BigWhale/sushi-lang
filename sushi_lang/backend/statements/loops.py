"""Loop statement emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_both_initialized

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Foreach, RangeExpr
    from sushi_lang.semantics.typesys import StructType


def emit_break(codegen: 'LLVMCodegen') -> None:
    """Emit break statement (jump to loop end)."""
    assert codegen.loop_stack, "checker guarantees inside-loop"
    _, break_bb, scope_boundary = codegen.loop_stack[-1]
    # Free heap-owning locals of the loop's own scopes before abandoning them; the
    # branch terminates this block, so pop_scope would otherwise skip their destructors.
    from sushi_lang.backend.statements.utils import emit_loop_exit_cleanup
    emit_loop_exit_cleanup(codegen, scope_boundary)
    codegen.builder.branch(break_bb)
    codegen.utils.after_terminator_unreachable()


def emit_continue(codegen: 'LLVMCodegen') -> None:
    """Emit continue statement (jump to loop condition)."""
    assert codegen.loop_stack, "checker guarantees inside-loop"
    cont_bb, _, scope_boundary = codegen.loop_stack[-1]
    from sushi_lang.backend.statements.utils import emit_loop_exit_cleanup
    emit_loop_exit_cleanup(codegen, scope_boundary)
    codegen.builder.branch(cont_bb)
    codegen.utils.after_terminator_unreachable()


def emit_foreach(codegen: 'LLVMCodegen', node: 'Foreach') -> None:
    """Emit foreach loop with iterator protocol."""
    from llvmlite import ir
    from sushi_lang.semantics.typesys import IteratorType, BuiltinType

    builder, func = require_both_initialized(codegen)
    if node.item_type is None:
        raise_internal_error("CE0015", message="foreach item_type not resolved by semantic analysis")
    codegen.utils.ensure_open_block()

    from sushi_lang.semantics.ast import RangeExpr
    if isinstance(node.iterable, RangeExpr):
        _emit_range_foreach(codegen, node, node.iterable)
        return

    iterator_value = codegen.expressions.emit_expr(node.iterable)
    iterator_type = IteratorType(element_type=node.item_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="__iter")
    codegen.builder.store(iterator_value, iterator_slot)

    is_string_iterator = (node.item_type == BuiltinType.STRING)

    zero = ir.Constant(codegen.types.i32, 0)

    from sushi_lang.semantics.ast import DotCall
    from sushi_lang.semantics.typesys import StructType
    is_hashmap_keys_or_values = False
    hashmap_type = None
    hashmap_method = None

    if isinstance(node.iterable, DotCall):
        if node.iterable.method in ("keys", "values", "entries"):
            # The SAME helper the HashMap emitters use, so this loop and
            # `try_emit_hashmap_method` cannot disagree about what a HashMap receiver is.
            # Asking by variable NAME made `get_map()??.keys()` fall through to the array
            # foreach and iterate zero entries with no diagnostic.
            from sushi_lang.backend.expressions.calls.utils import infer_generic_struct_type
            receiver_type = infer_generic_struct_type(
                codegen, node.iterable.receiver, "HashMap<")
            if isinstance(receiver_type, StructType) and receiver_type.name.startswith("HashMap<"):
                is_hashmap_keys_or_values = True
                hashmap_type = receiver_type
                hashmap_method = node.iterable.method

    if is_hashmap_keys_or_values:
        _emit_hashmap_foreach(codegen, node, iterator_slot, zero, hashmap_type, hashmap_method)
    elif is_string_iterator:
        _emit_string_iterator_foreach(codegen, node, iterator_slot, zero)
    else:
        _emit_array_foreach(codegen, node, iterator_slot, zero)


def _is_lines_iterator(codegen: 'LLVMCodegen', node: 'Foreach') -> bool:
    """Is this `foreach` walking a `File.lines()` iterator rather than a buffer?

    Decided STATICALLY, which is possible because `Iterator@(T)` is not a nameable type
    (CE2001): an iterator cannot be stored in a local, so it only ever appears here, in
    the loop that consumes it. The HashMap arm above reads its own shape the same way.

    It used to be a RUN-TIME test on the `length == -1` sentinel, with both loops emitted
    every time. That was already wasteful on an ordinary array walk, and once the lazy
    arm called a stdlib function rather than inlining libc, it broke outright: every
    program iterating a `string[]` referenced `sushi_io_files_fd_readln` and failed to
    link unless it happened to import <io/files>.

    The sentinel itself stays exactly as it was -- ruling R13 leaves everything about
    `lines()` for Phase 7 to decide.
    """
    from sushi_lang.semantics.ast import DotCall, MethodCall
    from sushi_lang.semantics.typesys import StructType, deref_type
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type

    if not isinstance(node.iterable, (DotCall, MethodCall)):
        return False
    if node.iterable.method != "lines":
        return False
    receiver_type = deref_type(infer_expr_semantic_type(codegen, node.iterable.receiver))
    return isinstance(receiver_type, StructType) and receiver_type.name == "File"


def _emit_string_iterator_foreach(codegen: 'LLVMCodegen', node: 'Foreach', iterator_slot: 'ir.Value', zero: 'ir.Constant') -> None:
    """Emit foreach over an iterator of strings: a `File.lines()` cursor, or a buffer."""
    if not _is_lines_iterator(codegen, node):
        _emit_array_foreach(codegen, node, iterator_slot, zero)
        return

    lines_loop_bb = codegen.func.append_basic_block(name="foreach.lines_loop")
    end_bb = codegen.func.append_basic_block(name="foreach.end")
    codegen.builder.branch(lines_loop_bb)

    _emit_stdin_lines_foreach(codegen, node, iterator_slot, zero, lines_loop_bb, end_bb)

    # The statement after the loop goes here. The array arm used to leave the builder
    # positioned at the end block for us; the lazy arm alone has to say so.
    codegen.builder.position_at_end(end_bb)


def _emit_stdin_lines_foreach(
    codegen: 'LLVMCodegen',
    node: 'Foreach',
    iterator_slot: 'ir.Value',
    zero: 'ir.Constant',
    stdin_loop_bb: 'ir.Block',
    end_bb: 'ir.Block'
) -> None:
    """Emit the lazy arm of `foreach`: a `File.lines()` iterator, read a line at a time.

    The sentinel is `length == -1`, which the caller has already branched on. The data
    slot holds a heap cell carrying the DESCRIPTOR, and one call to `fd_readln` per
    iteration answers the next line; an empty answer is end of file and ends the loop.

    This used to fork on a SECOND sentinel -- a null data slot meant stdin and reached
    libc `fgets` on the stdio handle, a non-null one meant a file and reached a different
    `fgets` -- and joined the two lines with a phi. `stdin` is an ordinary File over
    descriptor 0 now, so both arms and the phi are gone. Ruling R13 leaves everything
    else about `lines()` for Phase 7.
    """
    from llvmlite import ir
    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.functions import declare_stdlib_function
    from sushi_lang.sushi_stdlib.src.libc_declarations import declare_free
    from sushi_lang.sushi_stdlib.src.type_definitions import (
        get_maybe_type, get_result_type, get_string_type, get_unit_enum_type,
    )

    codegen.builder.position_at_end(stdin_loop_bb)
    stdin_cond_bb = codegen.func.append_basic_block(name="foreach.lines_cond")
    stdin_body_bb = codegen.func.append_basic_block(name="foreach.lines_body")

    codegen.builder.branch(stdin_cond_bb)
    codegen.builder.position_at_end(stdin_cond_bb)

    cell_ptr_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "fd_cell_ptr")
    cell = codegen.builder.load(cell_ptr_ptr, name="fd_cell")
    fd = codegen.builder.load(
        codegen.builder.bitcast(cell, codegen.types.i32.as_pointer(), name="fd_slot"),
        name="line_fd")

    string_ty = get_string_type()
    maybe_ty = get_maybe_type(string_ty)
    result_ty = get_result_type(maybe_ty, get_unit_enum_type())
    readln = declare_stdlib_function(codegen.module, "sushi_io_files_fd_readln",
                                     result_ty, [codegen.types.i32])
    answer = codegen.builder.call(readln, [fd], name="line_result")

    # The END of the loop is the Maybe's None tag, and NOT an empty string: a blank line
    # is `Some("")` and used to truncate the file here. A read failure also stops the
    # loop, because `foreach` has nowhere to put a Result -- an `Iterator@(string)`
    # yields a bare string, which is the hole R13 records.
    payload = codegen.builder.alloca(result_ty.elements[1], name="line_payload")
    codegen.builder.store(codegen.builder.extract_value(answer, 1), payload)
    maybe_value = codegen.builder.load(
        codegen.builder.bitcast(payload, maybe_ty.as_pointer(), name="line_maybe_ptr"),
        name="line_maybe")

    line_payload = codegen.builder.alloca(maybe_ty.elements[1], name="line_some_payload")
    codegen.builder.store(codegen.builder.extract_value(maybe_value, 1), line_payload)
    line_value = codegen.builder.load(
        codegen.builder.bitcast(line_payload, string_ty.as_pointer(),
                                name="line_str_ptr"),
        name="line")

    failed = codegen.builder.icmp_signed(
        "!=", codegen.builder.extract_value(answer, 0), ir.Constant(codegen.types.i32, 0),
        name="line_failed")
    at_end = codegen.builder.icmp_signed(
        "!=", codegen.builder.extract_value(maybe_value, 0),
        ir.Constant(codegen.types.i32, 0), name="line_none")

    done_bb = codegen.func.append_basic_block(name="foreach.lines_done")
    codegen.builder.cbranch(codegen.builder.or_(failed, at_end, name="line_done"),
                            done_bb, stdin_body_bb)

    # The descriptor cell `sushi_file_lines` allocated is this loop's to release: the
    # iterator has no destructor, so every `lines()` used to leak sixteen bytes.
    codegen.builder.position_at_end(done_bb)
    codegen.builder.call(declare_free(codegen.module),
                         [codegen.builder.bitcast(cell, codegen.types.i8.as_pointer(),
                                                  name="fd_cell_bytes")])
    codegen.builder.branch(end_bb)

    codegen.builder.position_at_end(stdin_body_bb)
    codegen.loop_stack.append((stdin_cond_bb, end_bb, codegen.memory._scope_depth + 1))
    codegen.memory.push_scope()

    element_ll_type = codegen.types.ll_type(node.item_type)
    codegen.memory.create_local(node.item_name, element_ll_type, line_value, node.item_type)

    _emit_block(codegen, node.body)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    if codegen.builder.block.terminator is None:
        codegen.builder.branch(stdin_cond_bb)


def _emit_array_foreach(codegen: 'LLVMCodegen', node: 'Foreach', iterator_slot: 'ir.Value', zero: 'ir.Constant') -> None:
    """Emit foreach for regular array iterators (non-string types)."""
    from sushi_lang.backend import gep_utils

    end_bb = codegen.func.append_basic_block(name="foreach.end")
    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    _emit_array_foreach_body(codegen, node, iterator_slot, zero, length_ptr, end_bb)


def _emit_array_foreach_body(
    codegen: 'LLVMCodegen',
    node: 'Foreach',
    iterator_slot: 'ir.Value',
    zero: 'ir.Constant',
    length_ptr: 'ir.Value',
    end_bb: 'ir.Block'
) -> None:
    """Emit the array iteration loop body."""
    from llvmlite import ir
    from sushi_lang.backend import gep_utils

    cond_bb = codegen.func.append_basic_block(name="foreach.cond")
    body_bb = codegen.func.append_basic_block(name="foreach.body")

    codegen.builder.branch(cond_bb)

    codegen.builder.position_at_end(cond_bb)

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    current_index = codegen.builder.load(index_ptr, name="current_index")

    length = codegen.builder.load(length_ptr, name="length")

    has_next = codegen.builder.icmp_signed("<", current_index, length, name="has_next")
    codegen.builder.cbranch(has_next, body_bb, end_bb)

    codegen.builder.position_at_end(body_bb)
    codegen.loop_stack.append((cond_bb, end_bb, codegen.memory._scope_depth + 1))
    codegen.memory.push_scope()

    data_ptr_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    element_ptr = codegen.builder.gep(data_ptr, [current_index], name="element_ptr")

    previous_entry = _MISSING
    if node.item_borrow is not None:
        # Reference binding (#300): store the element POINTER, not a copy, so the slot has
        # a `peek`/`poke` parameter's shape. The `ReferenceType` flips every consumer at
        # once -- `is_reference_parameter` keys on nothing else.
        previous_entry = bind_element_reference(codegen, node.item_name, node.item_borrow,
                                                 node.item_type, element_ptr)
    else:
        element_value = codegen.builder.load(element_ptr, name=node.item_name)

        # A foreach item is a read-only BORROW: the loaded value aliases the array's buffer,
        # which the array destructor frees. So `register_cleanup=False`, or an owning element
        # is freed by both the item and the container (#139, #147).
        element_ll_type = codegen.types.ll_type(node.item_type)
        codegen.memory.create_local(node.item_name, element_ll_type, element_value, node.item_type,
                                    register_cleanup=False)

    incremented_index = codegen.builder.add(current_index, ir.Constant(codegen.types.i32, 1), name="next_index")
    codegen.builder.store(incremented_index, index_ptr)

    try:
        _emit_block(codegen, node.body)
    finally:
        if node.item_borrow is not None:
            unbind_element_reference(codegen, node.item_name, previous_entry)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    if codegen.builder.block.terminator is None:
        codegen.builder.branch(cond_bb)

    codegen.builder.position_at_end(end_bb)


def _emit_hashmap_foreach(
    codegen: 'LLVMCodegen',
    node: 'Foreach',
    iterator_slot: 'ir.Value',
    zero: 'ir.Constant',
    hashmap_type: 'StructType',
    method: str
) -> None:
    """Emit foreach for HashMap.keys(), HashMap.values(), and HashMap.entries() iterators."""
    from llvmlite import ir
    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.generics.hashmap.types import (
        get_entry_type, get_user_entry_type, ENTRY_OCCUPIED,
    )
    from sushi_lang.semantics.generics.hashmap import (
        extract_key_value_types, ensure_entry_type_in_struct_table,
    )

    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    is_entries = (method == "entries")
    if is_entries:
        element_type = ensure_entry_type_in_struct_table(codegen.struct_table, key_type, value_type)
    else:
        element_type = key_type if method == "keys" else value_type
    entry_field_index = 0 if method == "keys" else 1  # 0=key, 1=value in Entry<K,V>

    entry_type = get_entry_type(codegen, key_type, value_type)

    cond_bb = codegen.func.append_basic_block(name="foreach.hashmap_cond")
    check_occupied_bb = codegen.func.append_basic_block(name="foreach.hashmap_check_occupied")
    body_bb = codegen.func.append_basic_block(name="foreach.hashmap_body")
    increment_bb = codegen.func.append_basic_block(name="foreach.hashmap_increment")
    end_bb = codegen.func.append_basic_block(name="foreach.hashmap_end")

    codegen.builder.branch(cond_bb)

    codegen.builder.position_at_end(cond_bb)

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    current_index = codegen.builder.load(index_ptr, name="current_index")

    capacity_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "capacity_ptr")
    marked_capacity = codegen.builder.load(capacity_ptr, name="marked_capacity")

    capacity_mask = ir.Constant(codegen.types.i32, 0x1FFFFFFF)
    actual_capacity = codegen.builder.and_(marked_capacity, capacity_mask, name="actual_capacity")

    has_more = codegen.builder.icmp_signed("<", current_index, actual_capacity, name="has_more")
    codegen.builder.cbranch(has_more, check_occupied_bb, end_bb)

    codegen.builder.position_at_end(check_occupied_bb)

    data_ptr_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="entries_ptr_as_element")

    entry_ptr_type = ir.PointerType(entry_type)
    entries_ptr = codegen.builder.bitcast(data_ptr, entry_ptr_type, name="entries_ptr")

    current_entry_ptr = codegen.builder.gep(entries_ptr, [current_index], name="current_entry_ptr")

    state_ptr = gep_utils.gep_struct_field(codegen, current_entry_ptr, 2, "state_ptr")
    state = codegen.builder.load(state_ptr, name="entry_state")

    is_occupied = codegen.builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")
    codegen.builder.cbranch(is_occupied, body_bb, increment_bb)

    codegen.builder.position_at_end(body_bb)
    codegen.loop_stack.append((cond_bb, end_bb, codegen.memory._scope_depth + 1))
    codegen.memory.push_scope()

    # The item binding is a read-only BORROW of the map's entry, exactly as the array path
    # at _emit_array_foreach is: the shallow-loaded key/value aliases the buffers the map's
    # own destructor frees, so `register_cleanup=False` below keeps the map the sole owner.
    # Registering the binding as a second owner double-freed every owning key/value type.
    if is_entries:
        user_entry_llvm = get_user_entry_type(codegen, key_type, value_type)

        key_ptr = gep_utils.gep_struct_field(codegen, current_entry_ptr, 0, "entry_key_ptr")
        key_val = codegen.builder.load(key_ptr, name="entry_key")

        value_ptr = gep_utils.gep_struct_field(codegen, current_entry_ptr, 1, "entry_value_ptr")
        value_val = codegen.builder.load(value_ptr, name="entry_value")

        entry_val = ir.Constant(user_entry_llvm, ir.Undefined)
        entry_val = codegen.builder.insert_value(entry_val, key_val, 0, name="entry_with_key")
        entry_val = codegen.builder.insert_value(entry_val, value_val, 1, name="entry_with_value")

        element_ll_type = user_entry_llvm
        codegen.memory.create_local(node.item_name, element_ll_type, entry_val, element_type,
                                    register_cleanup=False)
        codegen.variable_types[node.item_name] = element_type
    else:
        element_ptr = gep_utils.gep_struct_field(codegen, current_entry_ptr, entry_field_index, "element_ptr")

        if node.item_borrow is not None:
            # Reference binding (#300 phase 1): the entries buffer is heap storage, so
            # the GEP'd key/value pointer is bindable exactly like an array element's.
            # (`.entries()` bindings have NO address -- the user Entry is insert_value'd
            # above -- and the typecheck pass rejects the marker there with CE2423.)
            previous_entry = bind_element_reference(codegen, node.item_name, node.item_borrow,
                                                     element_type, element_ptr)
        else:
            element_value = codegen.builder.load(element_ptr, name=node.item_name)

            element_ll_type = codegen.types.ll_type(element_type)
            codegen.memory.create_local(node.item_name, element_ll_type, element_value, element_type,
                                        register_cleanup=False)

    try:
        _emit_block(codegen, node.body)
    finally:
        if node.item_borrow is not None:
            unbind_element_reference(codegen, node.item_name, previous_entry)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    if codegen.builder.block.terminator is None:
        codegen.builder.branch(increment_bb)

    codegen.builder.position_at_end(increment_bb)
    incremented_index = codegen.builder.add(current_index, ir.Constant(codegen.types.i32, 1), name="next_index")
    codegen.builder.store(incremented_index, index_ptr)
    codegen.builder.branch(cond_bb)

    codegen.builder.position_at_end(end_bb)


def _emit_range_foreach(codegen: 'LLVMCodegen', node: 'Foreach', range_expr: 'RangeExpr') -> None:
    """Emit optimized foreach loop for range expressions."""

    builder, func = require_both_initialized(codegen)
    codegen.utils.ensure_open_block()

    start_value = codegen.expressions.emit_expr(range_expr.start)
    start_i32 = codegen.utils.as_i32(start_value)

    end_value = codegen.expressions.emit_expr(range_expr.end)
    end_i32 = codegen.utils.as_i32(end_value)

    start_slot = codegen.builder.alloca(codegen.types.i32, name="range_start")
    codegen.builder.store(start_i32, start_slot)

    end_slot = codegen.builder.alloca(codegen.types.i32, name="range_end")
    codegen.builder.store(end_i32, end_slot)

    start_loaded = codegen.builder.load(start_slot, name="start_val")
    end_loaded = codegen.builder.load(end_slot, name="end_val")
    is_ascending = codegen.builder.icmp_signed("<", start_loaded, end_loaded, name="is_ascending")

    ascending_bb = codegen.func.append_basic_block(name="range.ascending")
    descending_bb = codegen.func.append_basic_block(name="range.descending")
    end_bb = codegen.func.append_basic_block(name="range.end")

    codegen.builder.cbranch(is_ascending, ascending_bb, descending_bb)

    codegen.builder.position_at_end(ascending_bb)
    _emit_range_loop_path(codegen, node, start_slot, end_slot, range_expr.inclusive, ascending=True, end_bb=end_bb)

    codegen.builder.position_at_end(descending_bb)
    _emit_range_loop_path(codegen, node, start_slot, end_slot, range_expr.inclusive, ascending=False, end_bb=end_bb)

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
    """Emit one direction of the range loop (ascending or descending)."""
    from llvmlite import ir

    end_val = codegen.builder.load(end_slot, name="end_val")
    if inclusive:
        if ascending:
            adjusted_end = codegen.builder.add(end_val, ir.Constant(codegen.types.i32, 1), name="adjusted_end")
        else:
            adjusted_end = codegen.builder.sub(end_val, ir.Constant(codegen.types.i32, 1), name="adjusted_end")
    else:
        adjusted_end = end_val

    cond_bb = codegen.func.append_basic_block(name=f"range.{'asc' if ascending else 'desc'}.cond")
    body_bb = codegen.func.append_basic_block(name=f"range.{'asc' if ascending else 'desc'}.body")
    incr_bb = codegen.func.append_basic_block(name=f"range.{'asc' if ascending else 'desc'}.incr")

    start_val = codegen.builder.load(start_slot, name="start_val")
    counter_slot = codegen.builder.alloca(codegen.types.i32, name=node.item_name)
    codegen.builder.store(start_val, counter_slot)

    codegen.builder.branch(cond_bb)

    codegen.builder.position_at_end(cond_bb)
    current_counter = codegen.builder.load(counter_slot, name=f"{node.item_name}_val")

    if ascending:
        condition = codegen.builder.icmp_signed("<", current_counter, adjusted_end, name="loop_cond")
    else:
        condition = codegen.builder.icmp_signed(">", current_counter, adjusted_end, name="loop_cond")

    codegen.builder.cbranch(condition, body_bb, end_bb)

    codegen.builder.position_at_end(body_bb)
    codegen.loop_stack.append((incr_bb, end_bb, codegen.memory._scope_depth + 1))
    codegen.memory.push_scope()

    element_ll_type = codegen.types.ll_type(node.item_type)
    counter_value = codegen.builder.load(counter_slot, name=node.item_name)
    codegen.memory.create_local(node.item_name, element_ll_type, counter_value, node.item_type)

    _emit_block(codegen, node.body)

    codegen.memory.pop_scope()
    codegen.loop_stack.pop()

    if codegen.builder.block.terminator is None:
        codegen.builder.branch(incr_bb)

    codegen.builder.position_at_end(incr_bb)
    current_val = codegen.builder.load(counter_slot, name="current_val")
    if ascending:
        next_val = codegen.builder.add(current_val, ir.Constant(codegen.types.i32, 1), name="next_val")
    else:
        next_val = codegen.builder.sub(current_val, ir.Constant(codegen.types.i32, 1), name="next_val")
    codegen.builder.store(next_val, counter_slot)
    codegen.builder.branch(cond_bb)


_MISSING = object()


def bind_element_reference(codegen: 'LLVMCodegen', name: str, borrow_mode: str,
                            element_type, element_ptr):
    """Bind a foreach item as a REFERENCE to the container's element (#300 phase 1)."""
    from sushi_lang.semantics.typesys import BorrowMode, ReferenceType
    mode = BorrowMode.POKE if borrow_mode == "poke" else BorrowMode.PEEK
    ref_type = ReferenceType(element_type, mode)
    codegen.memory.create_local(name, element_ptr.type, element_ptr, ref_type,
                                register_cleanup=False)
    previous = codegen.variable_types.get(name, _MISSING)
    codegen.variable_types[name] = ref_type
    return previous


def unbind_element_reference(codegen: 'LLVMCodegen', name: str, previous) -> None:
    """End a reference binding's `variable_types` entry at loop exit (#300)."""
    if previous is _MISSING:
        codegen.variable_types.pop(name, None)
    else:
        codegen.variable_types[name] = previous


def _emit_block(codegen: 'LLVMCodegen', block) -> None:
    """Helper to emit a block of statements."""
    from sushi_lang.backend.statements import StatementEmitter
    emitter = StatementEmitter(codegen)
    emitter.emit_block(block)
