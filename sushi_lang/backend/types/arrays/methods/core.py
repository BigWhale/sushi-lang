"""Dynamic array core methods."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import DynamicArrayNew, DynamicArrayFrom
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType
from sushi_lang.backend import gep_utils
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def _infer_builtin_type_from_llvm(llvm_type: ir.Type) -> BuiltinType:
    """Infer BuiltinType from LLVM type using dispatch table."""
    if isinstance(llvm_type, ir.IntType):
        width_to_builtin = {
            32: BuiltinType.I32,
            8: BuiltinType.I8,
            16: BuiltinType.I16,
            64: BuiltinType.I64,
            1: BuiltinType.BOOL,
        }
        return width_to_builtin.get(llvm_type.width, BuiltinType.I32)

    return BuiltinType.I32


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
    """Emit from(array_literal) constructor for dynamic arrays."""
    from ..utils import create_dynamic_array_from_elements, emit_array_literal_elements

    # Evaluate all element expressions, deep-copying heap-owning aliases so the new array
    # and the source each own independent buffers (a bare-Name element aliases a live owner;
    # a fresh temp is the sole owner and moved in). Element type is derived per alias.
    elements = emit_array_literal_elements(codegen, expr.elements.elements, None)

    if not elements:
        raise NotImplementedError("Empty from() constructor not yet supported")

    from ..runs import element_llvm_type as read_element_llvm_type

    element_llvm_type = read_element_llvm_type(codegen, elements)
    element_type = _infer_builtin_type_from_llvm(element_llvm_type)

    # The DESCRIPTOR, by value -- what `ll_type(DynamicArrayType)` says a `T[]` is, and what
    # every other type's `emit_expr` yields. Returning a pointer to it made this one
    # expression disagree with `emit_expr` of a Name, so a value position took a pointer
    # (#281, #283) and an address position took a value. The RECEIVER path is the one place
    # that needs an address, and it takes one (`normalize_array_receiver`).
    return create_dynamic_array_from_elements(codegen, element_type, element_llvm_type, elements)


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
    element_size = memory.get_element_size_constant(codegen, element_llvm_type)

    need_growth = b.icmp_signed(">", needed, current_cap, name="extend_need_growth")
    with b.if_then(need_growth):
        # Grown to exactly what is needed. A doubling schedule buys nothing here, because
        # the whole length is known before the copy starts.
        total_bytes = b.mul(needed, element_size, name="extend_total_bytes")
        old_data = b.load(data_ptr_ptr, name="extend_old_data")
        raw = memory.emit_realloc_call(codegen, b.bitcast(old_data, ir.PointerType(codegen.types.i8)),
                                       total_bytes)
        b.store(b.bitcast(raw, ir.PointerType(element_llvm_type)), data_ptr_ptr)
        b.store(needed, cap_ptr)

    data_ptr = b.load(data_ptr_ptr, name="extend_data")
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

    need_growth = codegen.builder.icmp_unsigned(">=", current_len, current_cap)

    func = codegen.func
    before_if = codegen.builder.block

    with codegen.builder.if_then(need_growth):
        zero = ir.Constant(codegen.types.i32, 0)
        one = ir.Constant(codegen.types.i32, 1)
        two = ir.Constant(codegen.types.i32, 2)

        cap_is_zero = codegen.builder.icmp_unsigned("==", current_cap, zero)
        double_cap = codegen.builder.mul(current_cap, two)
        new_cap = codegen.builder.select(cap_is_zero, one, double_cap, name="new_cap")

        element_type = array_type.elements[2].pointee
        element_size = memory.get_element_size_constant(codegen, element_type)
        new_total_size = codegen.builder.mul(new_cap, element_size, name="new_total_size")

        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)

        typed_new_data_ptr = codegen.builder.bitcast(new_data_ptr, ir.PointerType(element_type), name="typed_new_data_ptr")

        codegen.builder.store(new_cap, cap_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        after_if = codegen.builder.block

    phi = codegen.builder.phi(data_ptr.type, name="data_ptr_phi")
    phi.add_incoming(data_ptr, before_if)
    if 'after_if' in locals():
        phi.add_incoming(typed_new_data_ptr, after_if)
    data_ptr = phi

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
        if needs_cleanup(element_semantic_type):
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
    one = ir.Constant(codegen.types.i32, 1)
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
        if needs_cleanup(element_semantic_type):
            loop_i = codegen.builder.alloca(codegen.types.i32, name="free_loop_i")
            codegen.builder.store(zero, loop_i)

            loop_cond_bb = codegen.builder.append_basic_block(name="free_loop_cond")
            loop_body_bb = codegen.builder.append_basic_block(name="free_loop_body")
            loop_end_bb = codegen.builder.append_basic_block(name="free_loop_end")

            codegen.builder.branch(loop_cond_bb)

            codegen.builder.position_at_end(loop_cond_bb)
            i_val = codegen.builder.load(loop_i, name="i_val")
            loop_cond = codegen.builder.icmp_unsigned("<", i_val, current_len, name="loop_cond")
            codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

            codegen.builder.position_at_end(loop_body_bb)
            i_val = codegen.builder.load(loop_i, name="i_val")
            element_ptr = codegen.builder.gep(old_data_ptr, [i_val], name="element_ptr")

            emit_value_destructor(codegen, element_ptr, element_semantic_type)

            i_next = codegen.builder.add(i_val, one, name="i_next")
            codegen.builder.store(i_next, loop_i)
            codegen.builder.branch(loop_cond_bb)

            codegen.builder.position_at_end(loop_end_bb)

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


def emit_dynamic_array_fill(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                            fill_value: ir.Value, element_type: 'Type') -> ir.Value:
    """Emit code to fill all elements of a dynamic array with a value.

    The argument is a BORROW: each slot takes its own deep copy, so one value can fill
    several arrays and stays usable afterwards. `fill` is the one container write that
    does not consume -- see `docs/stdlib/collections/arrays.md`.
    """
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

    with codegen.builder.if_then(codegen.builder.not_(is_empty)):
        loop_i = codegen.builder.alloca(codegen.types.i32, name="fill_loop_i")
        codegen.builder.store(zero, loop_i)

        loop_cond_bb = codegen.builder.append_basic_block(name="fill_loop_cond")
        loop_body_bb = codegen.builder.append_basic_block(name="fill_loop_body")
        loop_end_bb = codegen.builder.append_basic_block(name="fill_loop_end")

        codegen.builder.branch(loop_cond_bb)

        codegen.builder.position_at_end(loop_cond_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")
        loop_cond = codegen.builder.icmp_unsigned("<", i_val, current_len, name="loop_cond")
        codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

        codegen.builder.position_at_end(loop_body_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, i_val, "element_ptr")
        _store_fill_element(codegen, element_ptr, fill_value, element_type)

        i_next = codegen.builder.add(i_val, one, name="i_next")
        codegen.builder.store(i_next, loop_i)
        codegen.builder.branch(loop_cond_bb)

        codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_reverse(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType) -> ir.Value:
    """Emit code to reverse a dynamic array in-place."""
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    two = ir.Constant(codegen.types.i32, 2)

    is_trivial = codegen.builder.icmp_unsigned("<", current_len, two)

    with codegen.builder.if_then(codegen.builder.not_(is_trivial)):
        half_len = codegen.builder.udiv(current_len, two, name="half_len")

        element_type = array_type.elements[2].pointee
        temp_var = codegen.builder.alloca(element_type, name="temp")

        loop_i = codegen.builder.alloca(codegen.types.i32, name="reverse_loop_i")
        codegen.builder.store(zero, loop_i)

        loop_cond_bb = codegen.builder.append_basic_block(name="reverse_loop_cond")
        loop_body_bb = codegen.builder.append_basic_block(name="reverse_loop_body")
        loop_end_bb = codegen.builder.append_basic_block(name="reverse_loop_end")

        codegen.builder.branch(loop_cond_bb)

        codegen.builder.position_at_end(loop_cond_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")
        loop_cond = codegen.builder.icmp_unsigned("<", i_val, half_len, name="loop_cond")
        codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

        codegen.builder.position_at_end(loop_body_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")

        len_minus_one = codegen.builder.sub(current_len, one, name="len_minus_one")
        j_val = codegen.builder.sub(len_minus_one, i_val, name="j_val")

        left_ptr = gep_utils.gep_array_element(codegen, data_ptr, i_val, "left_ptr")
        right_ptr = gep_utils.gep_array_element(codegen, data_ptr, j_val, "right_ptr")

        left_val = codegen.builder.load(left_ptr, name="left_val")
        codegen.builder.store(left_val, temp_var)

        right_val = codegen.builder.load(right_ptr, name="right_val")
        codegen.builder.store(right_val, left_ptr)

        temp_val = codegen.builder.load(temp_var, name="temp_val")
        codegen.builder.store(temp_val, right_ptr)

        i_next = codegen.builder.add(i_val, one, name="i_next")
        codegen.builder.store(i_next, loop_i)
        codegen.builder.branch(loop_cond_bb)

        codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)


def _store_fill_element(codegen: 'LLVMCodegen', element_ptr: ir.Value,
                        fill_value: ir.Value, element_type: 'Type') -> None:
    """Put one copy of the fill value into one slot, for either array kind.

    The copy comes FIRST: it reads the source before the old element is freed, so a source
    that aliases the buffer about to go is still intact. `arr[i] := v` orders it the same
    way. Both steps are no-ops for a plain element type, where a shallow store IS the value.
    """
    from sushi_lang.backend.destructors import destroy_old_value
    from sushi_lang.backend.ownership import copy_out

    copy = copy_out(codegen, fill_value, element_type)
    destroy_old_value(codegen, element_ptr, element_type)
    codegen.builder.store(codegen.utils.cast_for_param(copy, element_ptr.type.pointee), element_ptr)


def emit_fixed_array_fill(codegen: 'LLVMCodegen', array_ptr: ir.Value, array_type: ir.ArrayType,
                          fill_value: ir.Value, element_type: 'Type') -> ir.Value:
    """Emit code to fill all elements of a fixed array with a value.

    The argument is a BORROW; see `emit_dynamic_array_fill`.
    """
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    array_size = ir.Constant(codegen.types.i32, array_type.count)

    loop_i = codegen.builder.alloca(codegen.types.i32, name="fill_loop_i")
    codegen.builder.store(zero, loop_i)

    loop_cond_bb = codegen.builder.append_basic_block(name="fill_loop_cond")
    loop_body_bb = codegen.builder.append_basic_block(name="fill_loop_body")
    loop_end_bb = codegen.builder.append_basic_block(name="fill_loop_end")

    codegen.builder.branch(loop_cond_bb)

    codegen.builder.position_at_end(loop_cond_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")
    loop_cond = codegen.builder.icmp_unsigned("<", i_val, array_size, name="loop_cond")
    codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

    codegen.builder.position_at_end(loop_body_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")
    element_ptr = codegen.builder.gep(array_ptr, [zero, i_val], name="element_ptr")
    _store_fill_element(codegen, element_ptr, fill_value, element_type)

    i_next = codegen.builder.add(i_val, one, name="i_next")
    codegen.builder.store(i_next, loop_i)
    codegen.builder.branch(loop_cond_bb)

    codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)


def emit_fixed_array_reverse(codegen: 'LLVMCodegen', array_ptr: ir.Value, array_type: ir.ArrayType) -> ir.Value:
    """Emit code to reverse a fixed array in-place."""
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    array_size = array_type.count

    if array_size < 2:
        return ir.Constant(codegen.types.i32, 0)

    half_len = ir.Constant(codegen.types.i32, array_size // 2)
    array_size_const = ir.Constant(codegen.types.i32, array_size)

    element_type = array_type.element
    temp_var = codegen.builder.alloca(element_type, name="temp")

    loop_i = codegen.builder.alloca(codegen.types.i32, name="reverse_loop_i")
    codegen.builder.store(zero, loop_i)

    loop_cond_bb = codegen.builder.append_basic_block(name="reverse_loop_cond")
    loop_body_bb = codegen.builder.append_basic_block(name="reverse_loop_body")
    loop_end_bb = codegen.builder.append_basic_block(name="reverse_loop_end")

    codegen.builder.branch(loop_cond_bb)

    codegen.builder.position_at_end(loop_cond_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")
    loop_cond = codegen.builder.icmp_unsigned("<", i_val, half_len, name="loop_cond")
    codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

    codegen.builder.position_at_end(loop_body_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")

    size_minus_one = codegen.builder.sub(array_size_const, one, name="size_minus_one")
    j_val = codegen.builder.sub(size_minus_one, i_val, name="j_val")

    left_ptr = codegen.builder.gep(array_ptr, [zero, i_val], name="left_ptr")
    right_ptr = codegen.builder.gep(array_ptr, [zero, j_val], name="right_ptr")

    left_val = codegen.builder.load(left_ptr, name="left_val")
    codegen.builder.store(left_val, temp_var)

    right_val = codegen.builder.load(right_ptr, name="right_val")
    codegen.builder.store(right_val, left_ptr)

    temp_val = codegen.builder.load(temp_var, name="temp_val")
    codegen.builder.store(temp_val, right_ptr)

    i_next = codegen.builder.add(i_val, one, name="i_next")
    codegen.builder.store(i_next, loop_i)
    codegen.builder.branch(loop_cond_bb)

    codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)
