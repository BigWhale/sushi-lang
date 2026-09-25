"""Loop statement emission for the Sushi language compiler."""
from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING, Callable, Iterator
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_both_initialized
from sushi_lang.backend.memory.allocas import entry_alloca

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
    codegen.memory.emit_exit_cleanup(scope_boundary)
    codegen.builder.branch(break_bb)
    codegen.utils.after_terminator_unreachable()


def emit_continue(codegen: 'LLVMCodegen') -> None:
    """Emit continue statement (jump to loop condition)."""
    assert codegen.loop_stack, "checker guarantees inside-loop"
    cont_bb, _, scope_boundary = codegen.loop_stack[-1]
    codegen.memory.emit_exit_cleanup(scope_boundary)
    codegen.builder.branch(cont_bb)
    codegen.utils.after_terminator_unreachable()


class LoopFrame:
    """The loop body's exit actions, run on every exit path before the frame closes."""

    def __init__(self) -> None:
        """Start with no exit action."""
        self._on_exit: list[Callable[[], None]] = []

    def on_exit(self, action: Callable[[], None]) -> None:
        """Run `action` when the body ends, also when its emission raises."""
        self._on_exit.append(action)

    def _run_exit_actions(self) -> None:
        for action in self._on_exit:
            action()


@contextmanager
def loop_frame(codegen: 'LLVMCodegen', continue_bb: 'ir.Block', break_bb: 'ir.Block',
               back_edge: 'ir.Block') -> Iterator[LoopFrame]:
    """Open the frame of one loop body at the current block, and close it on exit.

    The loop-stack entry is the contract with `emit_break` and `emit_continue`: an early
    exit cleans up only the scopes above the recorded depth. On exit the body scope and
    the entry are popped, and the back edge is added when the block has no terminator.
    """
    codegen.loop_stack.append((continue_bb, break_bb, codegen.memory.depth + 1))
    codegen.memory.push_scope()
    frame = LoopFrame()
    try:
        yield frame
    finally:
        frame._run_exit_actions()
    codegen.memory.pop_scope()
    codegen.loop_stack.pop()
    if codegen.builder.block.terminator is None:
        codegen.builder.branch(back_edge)


def emit_foreach(codegen: 'LLVMCodegen', node: 'Foreach') -> None:
    """Emit foreach loop with iterator protocol."""
    from llvmlite import ir
    from sushi_lang.semantics.typesys import IteratorType

    builder, func = require_both_initialized(codegen)
    if node.item_type is None:
        raise_internal_error("CE0015", message="foreach item_type not resolved by semantic analysis")
    codegen.utils.ensure_open_block()

    from sushi_lang.semantics.ast import RangeExpr
    if isinstance(node.iterable, RangeExpr):
        _emit_range_foreach(codegen, node, node.iterable)
        return

    # A `next()` protocol iterator (HANDLES.md ruling R21). Chosen statically, on the
    # stamp the typecheck pass left: every other arm walks contiguous storage, and this
    # one calls a method per iteration.
    if node.protocol_next is not None:
        _emit_protocol_foreach(codegen, node)
        return

    iterator_value = codegen.expressions.emit_expr(node.iterable)
    iterator_type = IteratorType(element_type=node.item_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = entry_alloca(codegen.builder, iterator_struct_type, name="__iter")
    codegen.builder.store(iterator_value, iterator_slot)

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
                codegen, node.iterable.receiver, "HashMap")
            if isinstance(receiver_type, StructType) and is_instance_of(receiver_type, "HashMap"):
                is_hashmap_keys_or_values = True
                hashmap_type = receiver_type
                hashmap_method = node.iterable.method

    if is_hashmap_keys_or_values:
        _emit_hashmap_foreach(codegen, node, iterator_slot, zero, hashmap_type, hashmap_method)
    else:
        _emit_array_foreach(codegen, node, iterator_slot, zero)


def _emit_protocol_foreach(codegen: 'LLVMCodegen', node: 'Foreach') -> None:
    """Emit `foreach(item in it)` where `it` carries `next()` answering `Maybe@(T)`.

    The iterator lives in a local of its own for the loop's lifetime, because `next()`
    moves a cursor: re-evaluating `node.iterable` per iteration would restart it. That
    local is registered through `register_owning_value`, the COMPLETE registry router,
    and NOT through `create_local`'s default -- a `Lines@(File)` owns a `BufReader@(File)`
    owning a `File`, and `register_local_cleanup` alone does not know a dynamic array, a
    `List@(T)` or an `Own@(T)` (#382). Every iterator before this phase was a non-owning
    cursor over somebody else's buffer, which is why this is the first arm that has to
    destroy one.

    The call itself is `node.protocol_next`, built and stamped by the typecheck pass, so
    it is emitted through the ordinary method-call dispatcher: the receiver mode, the
    symbol and the return type are all resolved once, there, and not a second time here.
    """
    from sushi_lang.backend.constants.error_codes import MAYBE_SOME_TAG
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type

    iter_name = node.protocol_iter_name
    assert iter_name is not None, "the typecheck pass names the iterator slot"

    iterator_value = codegen.expressions.emit_expr(node.iterable)
    iterator_type = infer_expr_semantic_type(codegen, node.iterable)

    cond_bb = codegen.func.append_basic_block(name="foreach.next_cond")
    body_bb = codegen.func.append_basic_block(name="foreach.next_body")
    end_bb = codegen.func.append_basic_block(name="foreach.next_end")

    # The iterator's scope is the loop's, so the slot goes in a scope of its own that
    # closes after the end block: `break` and `return` both leave through the loop-exit
    # cleanup, which walks exactly these scopes.
    codegen.memory.push_scope()
    iter_slot = codegen.memory.create_local(
        iter_name, iterator_value.type, iterator_value, iterator_type,
        register_cleanup=False)
    if iterator_type is not None:
        codegen.memory.register_owning_value(iter_name, iterator_type, iter_slot)
    codegen.variable_types[iter_name] = iterator_type

    codegen.builder.branch(cond_bb)
    codegen.builder.position_at_end(cond_bb)

    answer = codegen.expressions.emit_expr(node.protocol_next)
    has_next = emit_enum_tag_check(codegen, answer, MAYBE_SOME_TAG, "has_next")
    codegen.builder.cbranch(has_next, body_bb, end_bb)

    codegen.builder.position_at_end(body_bb)
    with loop_frame(codegen, continue_bb=cond_bb, break_bb=end_bb, back_edge=cond_bb):
        _bind_protocol_item(codegen, node, answer)
        _emit_block(codegen, node.body)

    codegen.builder.position_at_end(end_bb)
    codegen.memory.pop_scope()
    codegen.variable_types.pop(iter_name, None)


def _bind_protocol_item(codegen: 'LLVMCodegen', node: 'Foreach', answer: 'ir.Value') -> None:
    """Bind the item of a protocol `foreach` to the payload of the `Maybe` that `next()` answered."""
    # The payload is read HERE and not in the condition block: on the last iteration the
    # answer is a None, whose payload bytes are zeroed and mean nothing.
    item_ll_type = codegen.types.ll_type(node.item_type)
    _is_some, item_value = codegen.functions.extract_value_from_result_enum(
        answer, item_ll_type, node.item_type)

    # The item is the Maybe's payload, and the Maybe is a temporary nobody else frees --
    # so this binding OWNS what it holds, unlike an array item, which aliases the
    # container's buffer and is registered with no cleanup at all. The `??` binder needs
    # no exception: its generated `let T x = <item>??` spends the item through the
    # ownership seam like any `??` over a named wrapper (#548), so the scope exit skips it.
    item_slot = codegen.memory.create_local(
        node.item_name, item_ll_type, item_value, node.item_type,
        register_cleanup=False)
    if node.item_type is not None:
        codegen.memory.register_owning_value(node.item_name, node.item_type, item_slot)


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
    with loop_frame(codegen, continue_bb=cond_bb, break_bb=end_bb, back_edge=cond_bb) as frame:
        data_ptr_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
        data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

        element_ptr = codegen.builder.gep(data_ptr, [current_index], name="element_ptr")

        if node.item_borrow is not None:
            # Reference binding (#300): store the element POINTER, not a copy, so the slot
            # has a `peek`/`poke` parameter's shape. The `ReferenceType` flips every
            # consumer at once -- `is_reference_parameter` keys on nothing else.
            _bind_reference_until_exit(codegen, frame, node.item_name, node.item_borrow,
                                       node.item_type, element_ptr)
        else:
            element_value = codegen.builder.load(element_ptr, name=node.item_name)

            # A foreach item is a read-only BORROW: the loaded value aliases the array's
            # buffer, which the array destructor frees. So `register_cleanup=False`, or an
            # owning element is freed by both the item and the container (#139, #147).
            element_ll_type = codegen.types.ll_type(node.item_type)
            codegen.memory.create_local(node.item_name, element_ll_type, element_value,
                                        node.item_type, register_cleanup=False)

        incremented_index = codegen.builder.add(current_index, ir.Constant(codegen.types.i32, 1), name="next_index")
        codegen.builder.store(incremented_index, index_ptr)

        _emit_block(codegen, node.body)

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
        parse_hashmap_types, ensure_entry_type_in_struct_table,
    )

    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")

    is_entries = (method == "entries")
    if is_entries:
        element_type = ensure_entry_type_in_struct_table(
            codegen.struct_table, codegen.derived_methods, key_type, value_type)
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
    with loop_frame(codegen, continue_bb=cond_bb, break_bb=end_bb,
                    back_edge=increment_bb) as frame:
        # The item binding is a read-only BORROW of the map's entry, exactly as the array
        # path is: the shallow-loaded key/value aliases the buffers the map's own
        # destructor frees, so `register_cleanup=False` below keeps the map the sole
        # owner. Registering the binding as a second owner double-freed every owning
        # key/value type.
        if is_entries:
            user_entry_llvm = get_user_entry_type(codegen, key_type, value_type)

            key_ptr = gep_utils.gep_struct_field(codegen, current_entry_ptr, 0, "entry_key_ptr")
            key_val = codegen.builder.load(key_ptr, name="entry_key")

            value_ptr = gep_utils.gep_struct_field(codegen, current_entry_ptr, 1, "entry_value_ptr")
            value_val = codegen.builder.load(value_ptr, name="entry_value")

            entry_val = ir.Constant(user_entry_llvm, ir.Undefined)
            entry_val = codegen.builder.insert_value(entry_val, key_val, 0, name="entry_with_key")
            entry_val = codegen.builder.insert_value(entry_val, value_val, 1, name="entry_with_value")

            codegen.memory.create_local(node.item_name, user_entry_llvm, entry_val, element_type,
                                        register_cleanup=False)
            codegen.variable_types[node.item_name] = element_type
        else:
            element_ptr = gep_utils.gep_struct_field(codegen, current_entry_ptr, entry_field_index, "element_ptr")

            if node.item_borrow is not None:
                # Reference binding (#300 phase 1): the entries buffer is heap storage, so
                # the GEP'd key/value pointer is bindable exactly like an array element's.
                # (`.entries()` bindings have NO address -- the user Entry is
                # insert_value'd above -- and the typecheck pass rejects the marker there
                # with CE2423.)
                _bind_reference_until_exit(codegen, frame, node.item_name, node.item_borrow,
                                           element_type, element_ptr)
            else:
                element_value = codegen.builder.load(element_ptr, name=node.item_name)

                element_ll_type = codegen.types.ll_type(element_type)
                codegen.memory.create_local(node.item_name, element_ll_type, element_value,
                                            element_type, register_cleanup=False)

        _emit_block(codegen, node.body)

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
    start_i32 = codegen.utils.require_i32(start_value)

    end_value = codegen.expressions.emit_expr(range_expr.end)
    end_i32 = codegen.utils.require_i32(end_value)

    start_slot = entry_alloca(codegen.builder, codegen.types.i32, name="range_start")
    codegen.builder.store(start_i32, start_slot)

    end_slot = entry_alloca(codegen.builder, codegen.types.i32, name="range_end")
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
    counter_slot = entry_alloca(codegen.builder, codegen.types.i32, name=node.item_name)
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
    with loop_frame(codegen, continue_bb=incr_bb, break_bb=end_bb, back_edge=incr_bb):
        element_ll_type = codegen.types.ll_type(node.item_type)
        counter_value = codegen.builder.load(counter_slot, name=node.item_name)
        codegen.memory.create_local(node.item_name, element_ll_type, counter_value, node.item_type)
        _emit_block(codegen, node.body)

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
    from sushi_lang.semantics.param_modes import borrow_mode as read_borrow_mode
    from sushi_lang.semantics.typesys import ReferenceType
    ref_type = ReferenceType(element_type, read_borrow_mode(borrow_mode))
    codegen.memory.create_local(name, element_ptr.type, element_ptr, ref_type,
                                register_cleanup=False)
    previous = codegen.variable_types.get(name, _MISSING)
    codegen.variable_types[name] = ref_type
    return previous


def _bind_reference_until_exit(codegen: 'LLVMCodegen', frame: LoopFrame, name: str,
                               borrow_mode: str, element_type, element_ptr) -> None:
    """Bind a foreach item as a reference, and end the binding when the loop body ends."""
    previous = bind_element_reference(codegen, name, borrow_mode, element_type, element_ptr)
    frame.on_exit(lambda: unbind_element_reference(codegen, name, previous))


def unbind_element_reference(codegen: 'LLVMCodegen', name: str, previous) -> None:
    """End a reference binding's `variable_types` entry at loop exit (#300)."""
    if previous is _MISSING:
        codegen.variable_types.pop(name, None)
    else:
        codegen.variable_types[name] = previous


def _emit_block(codegen: 'LLVMCodegen', block) -> None:
    """Emit a block of statements. The one copy the statement emitters share."""
    from sushi_lang.backend.statements import StatementEmitter
    emitter = StatementEmitter(codegen)
    emitter.emit_block(block)
