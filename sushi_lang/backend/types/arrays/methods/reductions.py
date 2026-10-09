"""The numeric reductions of an array: `.min()`, `.max()` and `.add_up()`.

Each one is one counted walk over `data_ptr[0..count)`, for either array kind. The
typecheck pass admits a numeric element type alone (CE2128), so each element is a plain
value: it is loaded, and nothing is copied or dropped.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.types.arrays.bounds import emit_checked_maybe

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_array_extreme(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                       element_type: 'Type', method: str) -> ir.Value:
    """`.min()` or `.max()`: `Maybe.Some(best)`, or `Maybe.None()` when the array is empty.

    The best value starts as element 0. A later element `x` replaces it when `x < best`
    for `min`, and when `best < x` for `max`, by the `<` of the element type. So an equal
    value never replaces it, and a NaN neither replaces it nor is chosen after element 0.
    """
    from sushi_lang.backend.expressions.operators import emit_number_comparison

    builder = codegen.builder
    best_slot = entry_alloca(builder, data_ptr.type.pointee, name=f"{method}_best")

    def keep_better(element_ptr: ir.Value, _index: ir.Value) -> None:
        candidate = builder.load(element_ptr, name=f"{method}_candidate")
        best = builder.load(best_slot, name=f"{method}_current")
        lower, upper = (candidate, best) if method == "min" else (best, candidate)
        better = emit_number_comparison(codegen, "<", lower, upper, element_type)
        builder.store(builder.select(better, candidate, best), best_slot)

    def read_best() -> ir.Value:
        builder.store(builder.load(data_ptr, name=f"{method}_first"), best_slot)
        one = ir.Constant(codegen.types.i32, 1)
        emit_container_walk(codegen, gep_utils.gep_array_element(codegen, data_ptr, one,
                                                                 f"{method}_rest"),
                            builder.sub(count, one, name=f"{method}_rest_count"),
                            keep_better, prefix=method)
        return builder.load(best_slot, name=f"{method}_result")

    zero = ir.Constant(codegen.types.i32, 0)
    return emit_checked_maybe(codegen, zero, count, element_type, read_best, prefix=method)


def emit_array_add_up(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                      element_type: 'Type') -> ir.Value:
    """`.add_up()`: the elements added from left to right with the `+` of the element type.

    An empty array gives zero. An integer total wraps, and a float total follows IEEE.
    """
    from sushi_lang.backend.expressions.operators import emit_arithmetic

    builder = codegen.builder
    element_llvm_type = data_ptr.type.pointee
    total_slot = entry_alloca(builder, element_llvm_type, name="add_up_total")
    builder.store(ir.Constant(element_llvm_type, 0), total_slot)

    def add(element_ptr: ir.Value, _index: ir.Value) -> None:
        total = builder.load(total_slot, name="add_up_current")
        element = builder.load(element_ptr, name="add_up_element")
        builder.store(emit_arithmetic(codegen, "+", total, element, element_type),
                      total_slot)

    emit_container_walk(codegen, data_ptr, count, add, prefix="add_up")
    return builder.load(total_slot, name="add_up_result")
