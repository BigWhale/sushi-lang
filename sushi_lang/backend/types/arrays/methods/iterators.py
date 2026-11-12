"""The slots of a fixed array and of a dynamic array, for a cursor (#1259): the element count
and the element data. `backend/runtime/iterators.py` makes the value of them.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from llvmlite import ir
from sushi_lang.backend import gep_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def fixed_array_slots(count: int) -> Callable[['LLVMCodegen', ir.Value], tuple[ir.Value, ir.Value]]:
    """The reader of the length and the data of a `T[count]` at an address."""
    def slots(codegen: 'LLVMCodegen', held: ir.Value) -> tuple[ir.Value, ir.Value]:
        zero = ir.Constant(codegen.types.i32, 0)
        return (ir.Constant(codegen.types.i32, count),
                gep_utils.gep_fixed_array_element(codegen, held, zero))
    return slots


def descriptor_slots(codegen: 'LLVMCodegen', descriptor: ir.Value) -> tuple[ir.Value, ir.Value]:
    """The length and the data of a `{i32 len, i32 cap, T* data}` at an address."""
    b = codegen.builder
    length = b.load(gep_utils.gep_dynamic_array_len(codegen, descriptor), name="length")
    data = b.load(gep_utils.gep_dynamic_array_data(codegen, descriptor), name="data")
    return length, data
