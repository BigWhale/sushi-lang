"""Dynamic array core methods."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import DynamicArrayNew, DynamicArrayFrom
from sushi_lang.semantics.typesys import DynamicArrayType
from sushi_lang.backend import gep_utils
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_dynamic_array_new(codegen: 'LLVMCodegen', expr: DynamicArrayNew) -> ir.Value:
    """Emit the `new()` constructor: an empty `{len, cap, data}` descriptor, BY VALUE.

    `new()` names no element type, so the typecheck pass stamps the one its position expects.
    Returning a scalar placeholder instead made `new()` a value nowhere: a call argument and
    `.realise(new())` were CE0017, a `Result.Ok(new())` payload aborted at scope exit, and a
    rebind crashed outright (#460).
    """
    from ..utils import emit_empty_dynamic_array

    array_type = expr.resolved_type
    if not isinstance(array_type, DynamicArrayType):
        raise_internal_error("CE0042", type=type(array_type).__name__)

    return emit_empty_dynamic_array(codegen, codegen.types.ll_type(array_type.base_type))


def emit_dynamic_array_from(codegen: 'LLVMCodegen', expr: DynamicArrayFrom) -> ir.Value:
    """Emit from(array_literal) constructor for dynamic arrays.

    The element type is the one the typecheck pass stamped: the position's declared type
    (#544), else the type it inferred from the elements (#868). The LLVM layout cannot
    tell a u32 from an i32, a float from an int, or a string from any other struct, so a
    missing stamp is a gap in the typecheck pass and CE0042, never a guess.
    """
    from ..utils import create_dynamic_array_from_elements, emit_array_literal_elements

    stamped = expr.resolved_type
    if not isinstance(stamped, DynamicArrayType):
        raise_internal_error("CE0042", type=type(stamped).__name__)
    element_type = stamped.base_type

    # Evaluate all element expressions, deep-copying heap-owning aliases so the new array
    # and the source each own independent buffers (a bare-Name element aliases a live owner;
    # a fresh temp is the sole owner and moved in).
    elements = emit_array_literal_elements(codegen, expr.elements.elements, element_type)
    element_llvm_type = codegen.types.ll_type(element_type)

    # The DESCRIPTOR, by value -- what `ll_type(DynamicArrayType)` says a `T[]` is, and what
    # every other type's `emit_expr` yields. Returning a pointer to it made this one
    # expression disagree with `emit_expr` of a Name, so a value position took a pointer
    # (#281, #283) and an address position took a value. The RECEIVER path is the one place
    # that needs an address, and it takes one (`normalize_array_receiver`).
    return create_dynamic_array_from_elements(codegen, element_llvm_type, elements)


def emit_dynamic_array_len(codegen: 'LLVMCodegen', array_value: ir.Value, to_i1: bool) -> ir.Value:
    """Emit code to get the length of a dynamic array."""
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    len_value = codegen.builder.load(len_ptr, name="array_len")

    return codegen.utils.as_i1(len_value) if to_i1 else len_value


def emit_dynamic_array_capacity(codegen: 'LLVMCodegen', array_value: ir.Value, to_i1: bool) -> ir.Value:
    """Emit code to get the capacity of a dynamic array."""
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    cap_value = codegen.builder.load(cap_ptr, name="array_capacity")

    return codegen.utils.as_i1(cap_value) if to_i1 else cap_value


def emit_dynamic_array_extend(codegen: 'LLVMCodegen', array_value: ir.Value,
                              array_type: ir.LiteralStructType, source_data: ir.Value,
                              source_len: ir.Value, start: ir.Value, extent: ir.Value,
                              element_type, *, extent_is_end: bool = False) -> ir.Value:
    """Append `source[start .. start + count)` to the receiver, growing it once (#462).

    Once, not `count` times: a `.push()` loop reallocates on a doubling schedule and pays a
    bounds check and a capacity check per element, which is the cost this operation exists
    to remove.

    The range is CLAMPED to what the source can answer, the way the string twins clamp.
    """
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend.types.arrays.copy import clamp_range, emit_range_copy

    start, count = clamp_range(codegen, start, extent, source_len,
                               extent_is_end=extent_is_end)

    b = codegen.builder
    len_ptr = codegen.types.get_dynamic_array_len_ptr(b, array_value)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(b, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(b, array_value)

    current_len = b.load(len_ptr, name="extend_len")
    current_cap = b.load(cap_ptr, name="extend_cap")
    needed = b.add(current_len, count, name="extend_needed")

    element_llvm_type = array_type.elements[2].pointee
    # Grown to exactly what is needed. A doubling schedule buys nothing here, because the
    # whole length is known before the copy starts.
    data_ptr = memory.emit_grow_to_fit(
        codegen, data_ptr=b.load(data_ptr_ptr, name="extend_data"), data_ptr_ptr=data_ptr_ptr,
        cap_ptr=cap_ptr, current_cap=current_cap, count=needed,
        element_llvm_type=element_llvm_type, policy=memory.GrowPolicy.EXACT)
    dest = gep_utils.gep_array_element(codegen, data_ptr, current_len, "extend_dest")
    emit_range_copy(codegen, dest, source_data, start, count, element_type,
                    element_llvm_type, prefix="extend")
    b.store(needed, len_ptr)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_slice(codegen: 'LLVMCodegen', element_llvm_type: ir.Type,
                             source_data: ir.Value, source_len: ir.Value, start: ir.Value,
                             extent: ir.Value, element_type, *,
                             extent_is_end: bool = False) -> ir.Value:
    """A FRESH `T[]` holding `source[start .. start + count)` (#462).

    The allocation is the one `from([0; n])` uses, which is why it is a named helper. The
    range is CLAMPED to what the source can answer, the way the string twins clamp.
    """
    from sushi_lang.backend.types.arrays.copy import clamp_range, emit_range_copy
    from sushi_lang.backend.types.arrays.utils import emit_dynamic_array_of_length

    start, count = clamp_range(codegen, start, extent, source_len,
                               extent_is_end=extent_is_end)

    array_struct, data_ptr = emit_dynamic_array_of_length(codegen, element_llvm_type, count)
    emit_range_copy(codegen, data_ptr, source_data, start, count, element_type,
                    element_llvm_type, prefix="slice")
    return array_struct


def emit_dynamic_array_push(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                            element_value: ir.Value) -> ir.Value:
    """Emit code to append an element to a dynamic array."""
    from sushi_lang.backend.expressions import memory

    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(cap_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    data_ptr = memory.emit_grow_to_fit(
        codegen, data_ptr=data_ptr, data_ptr_ptr=data_ptr_ptr, cap_ptr=cap_ptr,
        current_cap=current_cap, count=current_len,
        element_llvm_type=array_type.elements[2].pointee, policy=memory.GrowPolicy.DOUBLE)

    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, current_len, "element_ptr")
    codegen.builder.store(element_value, element_ptr)

    new_len = codegen.builder.add(current_len, ir.Constant(codegen.types.i32, 1), name="new_len")
    codegen.builder.store(new_len, len_ptr)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_pop(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                           element_semantic_type: 'Type', to_i1: bool) -> ir.Value:
    """Emit code to remove the last element from a dynamic array, as `Maybe@(T)`.

    A pop from an empty array has no element, so the empty branch answers `Maybe.None()`
    and nothing has to be invented. It used to answer `Constant(element_type, 0)`: a real
    0 for an `i32[]`, indistinguishable from a genuinely popped one -- and for an
    AGGREGATE element not a valid initializer at all, so a `string[]` or a struct with an
    owning field was a CE0000 before it ever ran (#377). `List@(T).pop()` already
    answered `Maybe@(T)`, and so does `arr.get()`.

    The element is REMOVED, so the array stops owning it and the `Maybe.Some(...)` carries
    it out -- the opposite of `.get()`, which leaves the array as the owner.
    """
    from sushi_lang.backend.generics.maybe import emit_maybe_some, emit_maybe_none

    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

    empty_block = codegen.builder.function.append_basic_block("array_empty")
    non_empty_block = codegen.builder.function.append_basic_block("array_non_empty")
    merge_block = codegen.builder.function.append_basic_block("pop_merge")

    codegen.builder.cbranch(is_empty, empty_block, non_empty_block)

    codegen.builder.position_at_end(empty_block)
    none_result = emit_maybe_none(codegen, element_semantic_type)
    none_pred = codegen.builder.block
    codegen.builder.branch(merge_block)

    codegen.builder.position_at_end(non_empty_block)

    last_index = codegen.builder.sub(current_len, one, name="last_index")

    last_element_ptr = gep_utils.gep_array_element(codegen, data_ptr, last_index, "last_element_ptr")
    popped_element = codegen.builder.load(last_element_ptr, name="popped_element")

    new_len = codegen.builder.sub(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    some_result = emit_maybe_some(codegen, element_semantic_type, popped_element)
    some_pred = codegen.builder.block
    codegen.builder.branch(merge_block)

    codegen.builder.position_at_end(merge_block)
    result_phi = codegen.builder.phi(some_result.type, name="pop_result")
    result_phi.add_incoming(none_result, none_pred)
    result_phi.add_incoming(some_result, some_pred)

    return result_phi


def emit_dynamic_array_truncate(codegen: 'LLVMCodegen', array_value: ir.Value,
                                array_type: ir.LiteralStructType, new_len: ir.Value,
                                element_semantic_type: 'Type') -> ir.Value:
    """Keep the first `new_len` elements and destroy the rest. `clear()` is truncate(0).

    Truncate never grows: a count past the length is a no-op, and a negative count
    clamps to 0 the way the slice family clamps. Capacity and the data pointer stay --
    the buffer is kept for reuse, which is what separates this from `free()`.
    """
    from sushi_lang.backend.destructors import needs_cleanup, emit_value_destructor
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    builder = codegen.builder
    zero = ir.Constant(codegen.types.i32, 0)

    len_ptr = codegen.types.get_dynamic_array_len_ptr(builder, array_value)
    current_len = builder.load(len_ptr, name="current_len")

    is_negative = builder.icmp_signed("<", new_len, zero, name="truncate_negative")
    kept = builder.select(is_negative, zero, new_len, name="truncate_kept")

    shrinks = builder.icmp_signed("<", kept, current_len, name="truncate_shrinks")
    with builder.if_then(shrinks):
        if needs_cleanup(codegen, element_semantic_type):
            data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(builder, array_value)
            data_ptr = builder.load(data_ptr_ptr, name="truncate_data")
            dropped_ptr = builder.gep(data_ptr, [kept], name="truncate_dropped")
            dropped_count = builder.sub(current_len, kept, name="truncate_drop_count")
            emit_container_walk(
                codegen, dropped_ptr, dropped_count,
                lambda element_ptr, _i: emit_value_destructor(codegen, element_ptr,
                                                              element_semantic_type),
                null_guard=True, prefix="truncate")
        builder.store(kept, len_ptr)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_free(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                           element_semantic_type: 'Type') -> ir.Value:
    """Emit code to free all elements of a dynamic array and reset to empty state."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend.memory.heap import emit_malloc

    zero = ir.Constant(codegen.types.i32, 0)
    initial_capacity = ir.Constant(codegen.types.i32, 8)

    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    old_data_ptr = codegen.builder.load(data_ptr_ptr, name="old_data_ptr")

    element_type = array_type.elements[2].pointee

    null_ptr = ir.Constant(ir.PointerType(element_type), None)
    is_not_null = codegen.builder.icmp_unsigned("!=", old_data_ptr, null_ptr)

    with codegen.builder.if_then(is_not_null):
        from sushi_lang.backend.destructors import needs_cleanup, emit_value_destructor
        if needs_cleanup(codegen, element_semantic_type):
            from sushi_lang.backend.generics.container_walk import emit_container_walk
            emit_container_walk(
                codegen, old_data_ptr, current_len,
                lambda element_ptr, _i: emit_value_destructor(codegen, element_ptr,
                                                              element_semantic_type),
                prefix="free")

        void_ptr = codegen.builder.bitcast(old_data_ptr, ir.PointerType(codegen.types.i8), name="void_ptr")
        memory.emit_free_call(codegen, void_ptr)

    element_size = memory.get_element_size_constant(codegen, element_type)
    new_total_size = codegen.builder.mul(initial_capacity, element_size, name="new_total_size")
    new_data_ptr = emit_malloc(codegen, codegen.builder, new_total_size)

    typed_new_data_ptr = codegen.builder.bitcast(new_data_ptr, ir.PointerType(element_type), name="typed_new_data_ptr")

    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(initial_capacity, cap_ptr)
    codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_destroy(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                              array_semantic_type: 'Type') -> ir.Value:
    """Emit code to explicitly destroy a dynamic array (makes it unusable)."""
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    element_type = array_type.elements[2].pointee
    null_ptr = ir.Constant(ir.PointerType(element_type), None)
    is_not_null = codegen.builder.icmp_unsigned("!=", data_ptr, null_ptr)

    with codegen.builder.if_then(is_not_null):
        from sushi_lang.backend.destructors import emit_value_destructor
        emit_value_destructor(codegen, array_value, array_semantic_type)

    zero = ir.Constant(codegen.types.i32, 0)
    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(zero, cap_ptr)
    codegen.builder.store(null_ptr, data_ptr_ptr)

    return ir.Constant(codegen.types.i32, 0)

def emit_array_fill(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                    fill_value: ir.Value, element_type: 'Type') -> ir.Value:
    """Fill `data_ptr[0..count)` with a value, for either array kind.

    The argument is a BORROW: each slot takes its own deep copy, so one value can fill
    several arrays and stays usable afterwards. `fill` is the one container write that
    does not consume -- see `docs/stdlib/collections/arrays.md`.
    """
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    emit_container_walk(
        codegen, data_ptr, count,
        lambda element_ptr, _i: _store_fill_element(codegen, element_ptr, fill_value,
                                                    element_type),
        prefix="fill")
    return ir.Constant(codegen.types.i32, 0)


def _store_fill_element(codegen: 'LLVMCodegen', element_ptr: ir.Value,
                        fill_value: ir.Value, element_type: 'Type') -> None:
    """Put one copy of the fill value into one slot.

    The copy comes FIRST: it reads the source before the old element is freed, so a source
    that aliases the buffer about to go is still intact. `arr[i] := v` orders it the same
    way. Both steps are no-ops for a plain element type, where a shallow store IS the value.
    """
    from sushi_lang.backend.destructors import destroy_old_value
    from sushi_lang.backend.ownership import copy_out

    copy = copy_out(codegen, fill_value, element_type)
    destroy_old_value(codegen, element_ptr, element_type)
    codegen.builder.store(codegen.utils.cast_for_param(copy, element_ptr.type.pointee), element_ptr)


def emit_array_reverse(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value) -> ir.Value:
    """Reverse `data_ptr[0..count)` in place, for either array kind.

    A count known at compile time decides the short case here; a loaded one decides it at
    run time.
    """
    builder = codegen.builder
    two = ir.Constant(codegen.types.i32, 2)

    if isinstance(count, ir.Constant):
        if count.constant >= 2:
            _swap_halves(codegen, data_ptr, count,
                         ir.Constant(codegen.types.i32, count.constant // 2))
    else:
        with builder.if_then(builder.icmp_unsigned(">=", count, two, name="reverse_needed")):
            _swap_halves(codegen, data_ptr, count, builder.udiv(count, two, name="half_len"))

    return ir.Constant(codegen.types.i32, 0)


def _swap_halves(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                 half: ir.Value) -> None:
    """A walk over the LEFT half: each element swaps with its mirror `count - 1 - i`."""
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    builder = codegen.builder
    temp = entry_alloca(builder, data_ptr.type.pointee, name="temp")
    last = builder.sub(count, ir.Constant(codegen.types.i32, 1), name="last_index")

    def swap(left_ptr: ir.Value, index: ir.Value) -> None:
        mirror = builder.sub(last, index, name="mirror_index")
        right_ptr = gep_utils.gep_array_element(codegen, data_ptr, mirror, "right_ptr")
        builder.store(builder.load(left_ptr, name="left_val"), temp)
        builder.store(builder.load(right_ptr, name="right_val"), left_ptr)
        builder.store(builder.load(temp, name="temp_val"), right_ptr)

    emit_container_walk(codegen, data_ptr, half, swap, prefix="reverse")
