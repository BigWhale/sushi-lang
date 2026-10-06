"""`contains(v)`, `index_of(v)` and `index_of_from(v, start)`: one linear search.

The array kinds and `List@(T)` all call these emitters with `(data_ptr, count)`.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_search

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type

_SIGNED_INTS = (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64)
_FLOATS = (BuiltinType.F32, BuiltinType.F64)


def _normalize_needle(codegen: 'LLVMCodegen', needle: ir.Value,
                      element_semantic_type: 'Type') -> ir.Value:
    """The needle at the element's exact IR width, converted ONCE before the loop.

    The semantic pass already matched the TYPES; this only settles the IR width a
    context-typed literal may still carry (an i32-shaped 0 against a u8[] element,
    a double against an f32[]).
    """
    element_ll = codegen.types.ll_type(element_semantic_type)
    if needle.type == element_ll:
        return needle
    builder = codegen.builder
    if isinstance(element_ll, ir.IntType) and isinstance(needle.type, ir.IntType):
        if needle.type.width > element_ll.width:
            return builder.trunc(needle, element_ll, name="needle_narrow")
        if element_semantic_type in _SIGNED_INTS:
            return builder.sext(needle, element_ll, name="needle_widen")
        return builder.zext(needle, element_ll, name="needle_widen")
    if element_ll == ir.FloatType() and needle.type == ir.DoubleType():
        return builder.fptrunc(needle, element_ll, name="needle_narrow")
    if element_ll == ir.DoubleType() and needle.type == ir.FloatType():
        return builder.fpext(needle, element_ll, name="needle_widen")
    return needle


def _element_equals(codegen: 'LLVMCodegen', element_ptr: ir.Value, needle: ir.Value,
                    element_semantic_type: 'Type') -> ir.Value:
    """One element against the needle, as i1 -- the `==` the language defines.

    The semantic gate (CE2100) admits the `==` operator's set: a primitive, which
    compares here exactly as the operator does, and a struct or an enum, which asks
    the one equality seam.
    """
    builder = codegen.builder
    element = builder.load(element_ptr, name="search_elem")
    if not isinstance(element_semantic_type, BuiltinType):
        from sushi_lang.backend.types.contracts import emit_value_eq
        return emit_value_eq(codegen, element, needle, element_semantic_type)
    if element_semantic_type == BuiltinType.STRING:
        return codegen.runtime.strings.emit_string_comparison("==", element, needle)
    if element_semantic_type in _FLOATS:
        return builder.fcmp_ordered("==", element, needle, name="search_eq")
    if element_semantic_type == BuiltinType.BOOL:
        return builder.icmp_unsigned("==", codegen.utils.as_i1(element),
                                     codegen.utils.as_i1(needle), name="search_eq")
    return builder.icmp_unsigned("==", element, needle, name="search_eq")


def _search(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
            needle: ir.Value, element_semantic_type: 'Type') -> tuple[ir.Value, ir.Value]:
    needle = _normalize_needle(codegen, needle, element_semantic_type)
    return emit_container_search(
        codegen, data_ptr, count,
        lambda element_ptr, _index: _element_equals(codegen, element_ptr, needle,
                                                    element_semantic_type))


def emit_array_contains(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                        needle: ir.Value, element_semantic_type: 'Type',
                        to_i1: bool) -> ir.Value:
    """`contains(v)` as bool: i1 for a condition, i8 for a stored bool."""
    found, _ = _search(codegen, data_ptr, count, needle, element_semantic_type)
    if to_i1:
        return found
    return codegen.builder.zext(found, codegen.types.i8, name="contains_result")


def emit_array_index_of(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                        needle: ir.Value, element_semantic_type: 'Type') -> ir.Value:
    """`index_of(v)` as `Maybe@(i32)`: `Some(first match)`, else `None`."""
    found, index = _search(codegen, data_ptr, count, needle, element_semantic_type)
    return _maybe_index(codegen, found, index)


def emit_array_index_of_from(codegen: 'LLVMCodegen', data_ptr: ir.Value, count: ir.Value,
                             needle: ir.Value, start: ir.Value,
                             element_semantic_type: 'Type') -> ir.Value:
    """`index_of_from(v, start)` as `Maybe@(i32)`: the first match at an index >= start.

    `start` is clamped into `0..count` (D3, the rule of `.s()` and the bulk copy), so
    nothing traps: a negative start reads from 0, and a start at or past the end leaves
    an empty range and answers `None`. The search is the one walk over the shorter range
    `data[start..count)`, and its index is moved back by `start`.
    """
    builder = codegen.builder
    zero = ir.Constant(codegen.types.i32, 0)
    low = builder.select(builder.icmp_signed("<", start, zero, name="from_negative"),
                         zero, start, name="from_low")
    clamped = builder.select(builder.icmp_signed(">", low, count, name="from_past_end"),
                             count, low, name="from_start")
    rest = builder.sub(count, clamped, name="from_rest")
    rest_data = gep_utils.gep_array_element(codegen, data_ptr, clamped, "from_data")
    found, offset = _search(codegen, rest_data, rest, needle, element_semantic_type)
    index = builder.add(offset, clamped, name="from_index")
    return _maybe_index(codegen, found, index)


def _maybe_index(codegen: 'LLVMCodegen', found: ir.Value, index: ir.Value) -> ir.Value:
    """`Some(index)` when `found`, else `None`, as one `Maybe@(i32)` value."""
    from sushi_lang.backend.generics.maybe import emit_maybe_some, emit_maybe_none

    builder = codegen.builder
    some_bb = builder.append_basic_block(name="index_of_some")
    none_bb = builder.append_basic_block(name="index_of_none")
    merge_bb = builder.append_basic_block(name="index_of_merge")
    builder.cbranch(found, some_bb, none_bb)

    builder.position_at_end(some_bb)
    some_result = emit_maybe_some(codegen, BuiltinType.I32, index)
    some_pred = builder.block
    builder.branch(merge_bb)

    builder.position_at_end(none_bb)
    none_result = emit_maybe_none(codegen, BuiltinType.I32)
    none_pred = builder.block
    builder.branch(merge_bb)

    builder.position_at_end(merge_bb)
    result = builder.phi(some_result.type, name="index_of_result")
    result.add_incoming(some_result, some_pred)
    result.add_incoming(none_result, none_pred)
    return result
