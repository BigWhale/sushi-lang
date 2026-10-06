"""List<T> search methods: contains(), index_of(), index_of_from().

The emitters of the array searches, over the list's `(data, len)`: one rule and one walk
for every container.
"""

from typing import Any

import llvmlite.ir as ir

from sushi_lang.semantics.typesys import StructType
from sushi_lang.backend.types.arrays.methods import search

from .types import extract_element_type, get_list_data_ptr, get_list_len_ptr


def _data_len_and_needle(codegen: Any, expr: Any, list_ptr: ir.Value,
                         list_type: StructType) -> tuple[ir.Value, ir.Value, ir.Value, Any]:
    """The list's data pointer and length, and the needle, a BORROW (#475)."""
    from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg

    element_type = extract_element_type(list_type, codegen)
    count = codegen.builder.load(get_list_len_ptr(codegen.builder, list_ptr), name="search_len")
    data = codegen.builder.load(get_list_data_ptr(codegen.builder, list_ptr), name="search_data")
    needle = emit_borrowed_arg(codegen, expr.args[0], element_type)
    return data, count, needle, element_type


def emit_list_contains(codegen: Any, expr: Any, list_ptr: ir.Value,
                       list_type: StructType) -> ir.Value:
    """`list.contains(v)`: the `i1` answer, shaped by the table's bool flag."""
    data, count, needle, element_type = _data_len_and_needle(codegen, expr, list_ptr, list_type)
    return search.emit_array_contains(codegen, data, count, needle, element_type, to_i1=True)


def emit_list_index_of(codegen: Any, expr: Any, list_ptr: ir.Value,
                       list_type: StructType) -> ir.Value:
    """`list.index_of(v)` as `Maybe@(i32)`."""
    data, count, needle, element_type = _data_len_and_needle(codegen, expr, list_ptr, list_type)
    return search.emit_array_index_of(codegen, data, count, needle, element_type)


def emit_list_index_of_from(codegen: Any, expr: Any, list_ptr: ir.Value,
                            list_type: StructType) -> ir.Value:
    """`list.index_of_from(v, start)` as `Maybe@(i32)`; the start is clamped."""
    data, count, needle, element_type = _data_len_and_needle(codegen, expr, list_ptr, list_type)
    start = codegen.utils.require_i32(codegen.expressions.emit_expr(expr.args[1]))
    return search.emit_array_index_of_from(codegen, data, count, needle, start, element_type)
