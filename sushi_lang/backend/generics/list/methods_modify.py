"""List<T> modification methods: push(), pop(), get(), first(), last(), clear(), insert(), remove()."""

from typing import Any, Sequence
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_len_ptr, get_list_capacity_ptr, get_list_element_type, extract_element_type, get_list_data_ptr, list_slots
from sushi_lang.backend.generics.container_table import blank
from sushi_lang.backend.types.arrays.methods.safe_access import emit_array_get_maybe


def emit_list_push(codegen: Any, list_ptr: ir.Value, _list_type: StructType,
                   args: Sequence[ir.Value]) -> ir.Value:
    """`list.push(element)`: the element goes last, after the growth. The element is
    `Owned`: the List holds it from here."""
    emit_list_append(codegen, list_ptr, args[0])
    return blank(codegen)


def emit_list_append(codegen: Any, list_ptr: ir.Value, element_value: ir.Value) -> None:
    """Grow the list at `list_ptr` to fit one more element, then store `element_value` last.
    An element that moves by address is the address of its value, and its bytes are copied.

    The list owns the element from here: the caller has settled its ownership.
    """
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.types.arrays.runs import emit_part_into

    element_llvm_type = get_list_element_type(codegen, list_ptr.type.pointee)
    len_ptr = get_list_len_ptr(codegen.builder, list_ptr)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_ptr)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_ptr)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    data_ptr = memory.emit_grow_to_fit(
        codegen, data_ptr=data_ptr, data_ptr_ptr=data_ptr_ptr, cap_ptr=capacity_ptr,
        current_cap=current_cap, count=current_len, element_llvm_type=element_llvm_type,
        policy=memory.GrowPolicy.DOUBLE)

    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, current_len, "element_ptr")
    emit_part_into(codegen, element_value, element_ptr)

    new_len = codegen.builder.add(current_len, ir.Constant(codegen.types.i32, 1), name="new_len")
    codegen.builder.store(new_len, len_ptr)


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


def emit_list_get(codegen: Any, list_ptr: ir.Value, list_type: StructType,
                  args: Sequence[ir.Value]) -> ir.Value:
    """`list.get(index)`: the element as `Maybe@(T)`, or `Maybe.None` out of range."""
    length, data = list_slots(codegen, list_ptr)
    return emit_array_get_maybe(codegen, data, length, codegen.utils.require_i32(args[0]),
                                extract_element_type(list_type, codegen))


def emit_list_end(codegen: Any, list_ptr: ir.Value, list_type: StructType, *,
                  last: bool) -> ir.Value:
    """`list.first()` is `get(0)`, and `list.last()` is `get(len() - 1)`. An empty list gives
    the index -1 for `last`, which the bounds check turns into `Maybe.None()`."""
    length, data = list_slots(codegen, list_ptr)
    index = (codegen.builder.sub(length, ir.Constant(codegen.types.i32, 1), name="last_index")
             if last else ir.Constant(codegen.types.i32, 0))
    return emit_array_get_maybe(codegen, data, length, index,
                                extract_element_type(list_type, codegen))


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

    return blank(codegen)


def _emit_destroy_elements_loop(codegen: Any, data_ptr: ir.Value, count: ir.Value, element_type: Any) -> None:
    """Destroy every element in data[0..count) with the recursive destructor."""
    from sushi_lang.backend.destructors import emit_value_destructor
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    def destroy(element_ptr: ir.Value, _index: ir.Value) -> None:
        emit_value_destructor(codegen, element_ptr, element_type)

    emit_container_walk(codegen, data_ptr, count, destroy, prefix="destroy")


def emit_list_insert(codegen: Any, list_ptr: ir.Value, list_type: StructType,
                     args: Sequence[ir.Value]) -> ir.Value:
    """`list.insert(index, element)` as `Result@(~, StdError)`. A refused index still owns
    the element, and the Err path destroys it (#869)."""
    from sushi_lang.backend.generics.container_shift import emit_descriptor_insert

    element_llvm_type = get_list_element_type(codegen, list_ptr.type.pointee)
    return emit_descriptor_insert(codegen, list_ptr, codegen.utils.require_i32(args[0]),
                                  args[1], extract_element_type(list_type, codegen),
                                  element_llvm_type)


def emit_list_remove(codegen: Any, list_ptr: ir.Value, list_type: StructType,
                     args: Sequence[ir.Value]) -> ir.Value:
    """`list.remove(index)`: the element as `Maybe@(T)`; the List no longer holds it."""
    from sushi_lang.backend.generics.container_shift import emit_descriptor_remove

    element_llvm_type = get_list_element_type(codegen, list_ptr.type.pointee)
    return emit_descriptor_remove(codegen, list_ptr, codegen.utils.require_i32(args[0]),
                                  extract_element_type(list_type, codegen), element_llvm_type)
