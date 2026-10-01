"""`insert(i, v)` and `remove(i)` over a `{i32 len, i32 cap, T* data}` descriptor.

A `List@(T)` and a `T[]` have the same descriptor, so the two containers share these two
emitters. Each caller evaluates its own arguments first and consumes the element, so a
refused index still owns the element and the Err path destroys it (#869).
"""

from typing import Any

import llvmlite.ir as ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.types.arrays.bounds import emit_bounds_check, emit_checked_maybe


def _emit_shift(codegen: Any, data_ptr: ir.Value, dest_index: ir.Value,
                src_index: ir.Value, count: ir.Value, element_llvm_type: ir.Type) -> None:
    """Move `count` slots from `src_index` to `dest_index`. The ranges overlap: memmove."""
    from sushi_lang.backend.expressions import memory

    zero = ir.Constant(codegen.types.i32, 0)
    with codegen.builder.if_then(codegen.builder.icmp_signed(">", count, zero)):
        src_ptr = gep_utils.gep_array_element(codegen, data_ptr, src_index, "shift_src")
        dest_ptr = gep_utils.gep_array_element(codegen, data_ptr, dest_index, "shift_dest")
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        bytes_to_move = codegen.builder.mul(count, element_size, name="shift_bytes")
        memory.emit_memmove_bytes(codegen, dest_ptr, src_ptr, bytes_to_move)


def emit_descriptor_insert(codegen: Any, descriptor: ir.Value, index: ir.Value,
                           element_value: ir.Value, element_type: Any,
                           element_llvm_type: ir.Type) -> ir.Value:
    """Store a consumed element at `index`, and answer `Result@(~, StdError)`.

    `0 <= index <= len` is Ok (`index == len` appends). Any other index is Err, and the
    element is destroyed there. A growth can move the buffer, so the shift and the store
    use the data pointer that the growth answers.
    """
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    from sushi_lang.backend.generics.result_builder import (
        build_err_from_return_type, build_ok_variant, intern_result,
    )
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.internals.errors import raise_internal_error

    std_error = codegen.enum_table.by_name.get("StdError")
    result_type = intern_result(codegen, BuiltinType.BLANK, std_error) if std_error is not None else None
    error_tag = std_error.get_variant_index("Error") if std_error is not None else None
    if result_type is None or std_error is None or error_tag is None:
        raise_internal_error("CE0091", type="Result@(~, StdError)")

    len_ptr = gep_utils.gep_dynamic_array_len(codegen, descriptor)
    cap_ptr = gep_utils.gep_dynamic_array_cap(codegen, descriptor)
    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, descriptor)
    current_len = codegen.builder.load(len_ptr, name="insert_len")
    current_cap = codegen.builder.load(cap_ptr, name="insert_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="insert_data")

    err_state: dict = {}

    def on_fail() -> None:
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

    emit_bounds_check(codegen, index, current_len, prefix="insert", on_fail=on_fail,
                      inclusive=True)
    end_block = err_state["end"]

    data_ptr = memory.emit_grow_to_fit(
        codegen, data_ptr=data_ptr, data_ptr_ptr=data_ptr_ptr, cap_ptr=cap_ptr,
        current_cap=current_cap, count=current_len, element_llvm_type=element_llvm_type,
        policy=memory.GrowPolicy.DOUBLE)

    one = ir.Constant(codegen.types.i32, 1)
    index_plus_one = codegen.builder.add(index, one, name="index_plus_one")
    num_to_move = codegen.builder.sub(current_len, index, name="num_to_move")
    _emit_shift(codegen, data_ptr, index_plus_one, index, num_to_move, element_llvm_type)

    insert_ptr = gep_utils.gep_array_element(codegen, data_ptr, index, "insert_ptr")
    codegen.builder.store(element_value, insert_ptr)
    codegen.builder.store(codegen.builder.add(current_len, one, name="new_len"), len_ptr)

    ok_enum = build_ok_variant(codegen, result_type, ir.Constant(codegen.types.i32, 0))
    ok_block = codegen.builder.block
    codegen.builder.branch(end_block)

    codegen.builder.position_at_end(end_block)
    result_phi = codegen.builder.phi(ok_enum.type, name="insert_result")
    result_phi.add_incoming(err_state["value"], err_state["block"])
    result_phi.add_incoming(ok_enum, ok_block)
    return result_phi


def emit_descriptor_remove(codegen: Any, descriptor: ir.Value, index: ir.Value,
                           element_type: Any, element_llvm_type: ir.Type) -> ir.Value:
    """Take the element at `index` out, and answer `Maybe@(T)`.

    `Maybe.Some` carries the element's ownership to the caller, because the element falls
    outside `[0, len)`. An index out of range answers `Maybe.None`, and nothing moves.
    """
    len_ptr = gep_utils.gep_dynamic_array_len(codegen, descriptor)
    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, descriptor)
    current_len = codegen.builder.load(len_ptr, name="remove_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="remove_data")

    def remove_element() -> ir.Value:
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index, "element_ptr")
        element_value = codegen.builder.load(element_ptr, name="removed_element")
        one = ir.Constant(codegen.types.i32, 1)
        index_plus_one = codegen.builder.add(index, one, name="index_plus_one")
        num_to_move = codegen.builder.sub(current_len, index_plus_one, name="num_to_move")
        _emit_shift(codegen, data_ptr, index, index_plus_one, num_to_move, element_llvm_type)
        codegen.builder.store(codegen.builder.sub(current_len, one, name="new_len"), len_ptr)
        return element_value

    return emit_checked_maybe(codegen, index, current_len, element_type, remove_element,
                              prefix="remove")
