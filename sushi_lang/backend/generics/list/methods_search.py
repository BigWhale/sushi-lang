"""List<T> search methods: contains(), index_of(), index_of_from().

The emitters of the array searches, over the list's `(data, len)`: one rule and one walk
for every container. The needle is a borrow (#475).
"""

from typing import Any, Sequence

import llvmlite.ir as ir

from sushi_lang.semantics.typesys import StructType
from sushi_lang.backend.types.arrays.methods import search

from .types import extract_element_type, list_slots


def emit_list_contains(codegen: Any, list_ptr: ir.Value, list_type: StructType,
                       args: Sequence[ir.Value]) -> ir.Value:
    """`list.contains(v)`: the `i1` answer, shaped by the table's bool flag."""
    length, data = list_slots(codegen, list_ptr)
    return search.emit_array_contains(codegen, data, length, args[0],
                                      extract_element_type(list_type, codegen), to_i1=True)


def emit_list_index_of(codegen: Any, list_ptr: ir.Value, list_type: StructType,
                       args: Sequence[ir.Value]) -> ir.Value:
    """`list.index_of(v)` as `Maybe@(i32)`."""
    length, data = list_slots(codegen, list_ptr)
    return search.emit_array_index_of(codegen, data, length, args[0],
                                      extract_element_type(list_type, codegen))


def emit_list_index_of_from(codegen: Any, list_ptr: ir.Value, list_type: StructType,
                            args: Sequence[ir.Value]) -> ir.Value:
    """`list.index_of_from(v, start)` as `Maybe@(i32)`; the start is clamped."""
    length, data = list_slots(codegen, list_ptr)
    return search.emit_array_index_of_from(codegen, data, length, args[0],
                                           codegen.utils.require_i32(args[1]),
                                           extract_element_type(list_type, codegen))
