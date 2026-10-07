"""`starts_with(prefix)` and `eq_range(start, other)`: one range comparison, two spellings."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_search
from .search import _element_equals

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_array_eq_range(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                        start: ir.Value, other_data: ir.Value, other_len: ir.Value,
                        element_semantic_type: 'Type', to_i1: bool) -> ir.Value:
    """Whether `data[start .. start + other_len)` equals `other_data[0 .. other_len)`.

    `starts_with` is the call with `start` 0. A range outside `0 .. count` answers false
    before any element is read. The bound reads `other_len <= count - start`, so an
    overflow of `start + other_len` cannot occur. The comparison is the counted search
    for the first MISMATCH, so it stops there.
    """
    builder = codegen.builder
    i32 = codegen.types.i32
    zero = ir.Constant(i32, 0)

    start_ok = builder.and_(builder.icmp_signed(">=", start, zero, name="eq_range_start_ok"),
                            builder.icmp_signed("<=", start, count, name="eq_range_start_in"),
                            name="eq_range_start_valid")
    room = builder.sub(count, start, name="eq_range_room")
    fits = builder.icmp_signed("<=", other_len, room, name="eq_range_fits")
    inside = builder.and_(start_ok, fits, name="eq_range_inside")
    outside_pred = builder.block

    compare_bb = builder.append_basic_block(name="eq_range_compare")
    merge_bb = builder.append_basic_block(name="eq_range_merge")
    builder.cbranch(inside, compare_bb, merge_bb)

    builder.position_at_end(compare_bb)

    def differs(other_ptr: ir.Value, index: ir.Value) -> ir.Value:
        at = builder.add(start, index, name="eq_range_at")
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, at, "eq_range_elem")
        other = builder.load(other_ptr, name="eq_range_other")
        equal = _element_equals(codegen, element_ptr, other, element_semantic_type)
        return builder.not_(equal, name="eq_range_differs")

    mismatch, _ = emit_container_search(codegen, other_data, other_len, differs,
                                        prefix="eq_range")
    equal_all = builder.not_(mismatch, name="eq_range_equal")
    compare_pred = builder.block
    builder.branch(merge_bb)

    builder.position_at_end(merge_bb)
    answer = builder.phi(ir.IntType(1), name="eq_range_result")
    answer.add_incoming(ir.Constant(ir.IntType(1), 0), outside_pred)
    answer.add_incoming(equal_all, compare_pred)
    if to_i1:
        return answer
    return builder.zext(answer, codegen.types.i8, name="eq_range_bool")
