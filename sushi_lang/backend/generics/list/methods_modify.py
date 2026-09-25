"""List<T> modification methods: push(), pop(), get(), clear(), insert(), remove()."""

from typing import Any
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_len_ptr, get_list_capacity_ptr, get_list_element_type, extract_element_type, get_list_data_ptr
from sushi_lang.backend.types.arrays.bounds import emit_bounds_check, emit_checked_maybe


def emit_list_push(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.push(element) - append element with auto-growth."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend import gep_utils

    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    data_ptr = memory.emit_grow_to_fit(
        codegen, data_ptr=data_ptr, data_ptr_ptr=data_ptr_ptr, cap_ptr=capacity_ptr,
        current_cap=current_cap, count=current_len, element_llvm_type=element_llvm_type,
        policy=memory.GrowPolicy.DOUBLE)

    # Evaluate element to push. The list stores it shallowly and frees it on
    # `.destroy()`/scope exit, so this is a consuming use: the seam decides whether the
    # source hands ownership over, detaches with a copy, or is rejected outright.
    from sushi_lang.backend.ownership import ConsumingUse, consume
    element_value = codegen.expressions.emit_expr(expr.args[0])
    element_value = consume(codegen, expr.args[0], element_value, element_type,
                            ConsumingUse.CONTAINER_INSERT)

    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, current_len, "element_ptr")
    codegen.builder.store(element_value, element_ptr)

    new_len = codegen.builder.add(current_len, ir.Constant(codegen.types.i32, 1), name="new_len")
    codegen.builder.store(new_len, len_ptr)

    return codegen.builder.load(list_alloca, name="updated_list")


def emit_list_pop(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.pop() - remove and return last element."""
    from sushi_lang.backend import gep_utils

    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    zero = ir.Constant(codegen.types.i32, 0)
    is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

    empty_block = codegen.func.append_basic_block("pop_empty")
    not_empty_block = codegen.func.append_basic_block("pop_not_empty")
    end_block = codegen.func.append_basic_block("pop_end")

    codegen.builder.cbranch(is_empty, empty_block, not_empty_block)

    codegen.builder.position_at_end(empty_block)
    from sushi_lang.backend.generics import maybe
    none_value = maybe.emit_maybe_none(codegen, element_type)
    codegen.builder.branch(end_block)
    empty_predecessor = codegen.builder.block

    codegen.builder.position_at_end(not_empty_block)

    one = ir.Constant(codegen.types.i32, 1)
    new_len = codegen.builder.sub(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, new_len, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element")

    some_value = maybe.emit_maybe_some(codegen, element_type, element_value)
    codegen.builder.branch(end_block)
    not_empty_predecessor = codegen.builder.block

    codegen.builder.position_at_end(end_block)
    maybe_type = maybe.get_maybe_enum_type(codegen, element_type)
    phi = codegen.builder.phi(maybe_type, name="pop_result")
    phi.add_incoming(none_value, empty_predecessor)
    phi.add_incoming(some_value, not_empty_predecessor)

    return phi


def emit_list_get(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.get(index) - safe element access."""
    from sushi_lang.backend import gep_utils

    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    index_value = codegen.expressions.emit_expr(expr.args[0])

    def read_element() -> ir.Value:
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
        return codegen.builder.load(element_ptr, name="element")

    # `.get()` READS and does not detach (#242), so the list still frees the element and
    # the returned `Maybe.Some(T)` carries a BORROW. `emit_list_pop` is the opposite: pop
    # decrements `len`, so the element falls outside the destructor's walk and that Maybe
    # is FRESH.
    return emit_checked_maybe(codegen, index_value, current_len, element_type, read_element)


def emit_list_clear(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.clear() - remove all elements but keep capacity."""
    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    _emit_destroy_elements_loop(codegen, data_ptr, current_len, element_type)

    zero = ir.Constant(codegen.types.i32, 0)
    codegen.builder.store(zero, len_ptr)

    return codegen.builder.load(list_alloca, name="cleared_list")


def _emit_destroy_elements_loop(codegen: Any, data_ptr: ir.Value, count: ir.Value, element_type: Any) -> None:
    """Destroy every element in data[0..count) with the recursive destructor."""
    from sushi_lang.backend.destructors import emit_value_destructor
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    def destroy(element_ptr: ir.Value, _index: ir.Value) -> None:
        emit_value_destructor(codegen, element_ptr, element_type)

    emit_container_walk(codegen, data_ptr, count, destroy, prefix="destroy")


def emit_list_insert(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.insert(index, element) - insert element at position."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend import gep_utils
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    from sushi_lang.backend.generics.result_builder import (
        build_err_from_return_type, build_ok_variant, intern_result,
    )
    from sushi_lang.internals.errors import raise_internal_error

    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    list_alloca = list_ptr

    # Both arguments are evaluated, and the element consumed, before the bounds check
    # (#869): a refused index still owns the element, and the Err path destroys it.
    from sushi_lang.backend.ownership import ConsumingUse, consume
    index_value = codegen.expressions.emit_expr(expr.args[0])
    element_value = codegen.expressions.emit_expr(expr.args[1])
    element_value = consume(codegen, expr.args[1], element_value, element_type,
                            ConsumingUse.CONTAINER_INSERT)

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    std_error = codegen.enum_table.by_name.get("StdError")
    result_type = intern_result(codegen, BuiltinType.BLANK, std_error) if std_error is not None else None
    error_tag = std_error.get_variant_index("Error") if std_error is not None else None
    if result_type is None or std_error is None or error_tag is None:
        raise_internal_error("CE0091", type="Result@(~, StdError)")

    err_state: dict = {}

    def on_fail() -> None:
        # An insert position may be `len` itself (an append), so the bound is inclusive.
        err_state["end"] = codegen.func.append_basic_block("insert_end")
        if needs_cleanup(codegen, element_type):
            refused_slot = codegen.memory.entry_alloca(element_llvm_type, "insert_refused")
            codegen.builder.store(element_value, refused_slot)
            emit_value_destructor(codegen, refused_slot, element_type)
        std_error_llvm_type = codegen.types.ll_type(std_error)
        error_value = ir.Constant(std_error_llvm_type,
                                  [ir.Constant(codegen.types.i32, error_tag),
                                   ir.Constant(std_error_llvm_type.elements[1], None)])
        err_state["value"] = build_err_from_return_type(codegen, result_type, error_value)
        err_state["block"] = codegen.builder.block
        codegen.builder.branch(err_state["end"])

    emit_bounds_check(codegen, index_value, current_len, prefix="insert", on_fail=on_fail,
                      inclusive=True)
    end_block = err_state["end"]

    data_ptr = memory.emit_grow_to_fit(
        codegen, data_ptr=data_ptr, data_ptr_ptr=data_ptr_ptr, cap_ptr=capacity_ptr,
        current_cap=current_cap, count=current_len, element_llvm_type=element_llvm_type,
        policy=memory.GrowPolicy.DOUBLE)

    # Shift [index, len) one slot to the right. The two ranges overlap: memmove.
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    num_to_move = codegen.builder.sub(current_len, index_value, name="num_to_move")
    has_elements_to_shift = codegen.builder.icmp_unsigned(">", num_to_move, zero)

    with codegen.builder.if_then(has_elements_to_shift):
        src_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "src_ptr")
        index_plus_one = codegen.builder.add(index_value, one, name="index_plus_one")
        dest_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_plus_one, "dest_ptr")
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        bytes_to_move = codegen.builder.mul(num_to_move, element_size, name="bytes_to_move")
        memory.emit_memmove_bytes(codegen, dest_ptr, src_ptr, bytes_to_move)

    insert_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "insert_ptr")
    codegen.builder.store(element_value, insert_ptr)

    new_len = codegen.builder.add(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    ok_enum = build_ok_variant(codegen, result_type, zero)
    ok_block = codegen.builder.block
    codegen.builder.branch(end_block)

    codegen.builder.position_at_end(end_block)
    result_phi = codegen.builder.phi(ok_enum.type, name="insert_result")
    result_phi.add_incoming(err_state["value"], err_state["block"])
    result_phi.add_incoming(ok_enum, ok_block)

    return result_phi


def emit_list_remove(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.remove(index) - remove element at position."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend import gep_utils

    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    index_value = codegen.expressions.emit_expr(expr.args[0])

    def remove_element() -> ir.Value:
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
        element_value = codegen.builder.load(element_ptr, name="removed_element")

        # Shift (index, len) one slot to the left. The two ranges overlap: memmove.
        zero = ir.Constant(codegen.types.i32, 0)
        one = ir.Constant(codegen.types.i32, 1)
        num_to_move = codegen.builder.sub(current_len, index_value, name="num_after_index")
        num_to_move = codegen.builder.sub(num_to_move, one, name="num_to_move")
        has_elements_to_shift = codegen.builder.icmp_unsigned(">", num_to_move, zero)

        with codegen.builder.if_then(has_elements_to_shift):
            index_plus_one = codegen.builder.add(index_value, one, name="index_plus_one")
            src_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_plus_one, "src_ptr")
            element_size = memory.get_element_size_constant(codegen, element_llvm_type)
            bytes_to_move = codegen.builder.mul(num_to_move, element_size, name="bytes_to_move")
            memory.emit_memmove_bytes(codegen, element_ptr, src_ptr, bytes_to_move)

        new_len = codegen.builder.sub(current_len, one, name="new_len")
        codegen.builder.store(new_len, len_ptr)
        return element_value

    return emit_checked_maybe(codegen, index_value, current_len, element_type, remove_element,
                              prefix="remove")
