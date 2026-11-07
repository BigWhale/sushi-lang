"""The value of an iterator from its `Aggregate`: one row for each source of the closed table
(docs/design/ir.md 8.7). The row builds the state and supplies the three functions
(`backend/runtime/iterators.py`), and it decides nothing: `heap` is the answer of `borrow`.
"""
from __future__ import annotations

from typing import Any, Callable

from llvmlite import ir

from sushi_lang.backend.generics.hashmap.methods.iterators import bucket_slots, bucket_source
from sushi_lang.backend.generics.list.types import list_slots
from sushi_lang.backend.runtime.iterators import (
    CursorSource, OwnedContainer, array_source, emit_lent_cursor, emit_new_iterator,
    emit_owned_cursor, emit_range_iterator)
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import operand_address, operand_value
from sushi_lang.backend.types.arrays.methods.iterators import descriptor_slots, fixed_array_slots
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.typesys import (
    ArrayType, DynamicArrayType, IteratorType, StructType, Type)

_Slots = Callable[[Any, ir.Value], tuple[ir.Value, ir.Value]]


def _elements_slots(container: Type) -> _Slots:
    if isinstance(container, DynamicArrayType):
        return descriptor_slots
    if isinstance(container, ArrayType):
        return fixed_array_slots(container.size)
    return list_slots


def _cursor(state: FunctionState, rvalue: r.Aggregate, source: CursorSource, container: Type,
            slots: _Slots, lent: bool) -> ir.Value:
    """A lent cursor reads the slots of the place of its part, and an owned one takes the
    container of its part."""
    part = rvalue.parts[0]
    kind = rvalue.kind
    if not isinstance(kind, r.AggIterator):
        state.fault(f"an iterator value of the kind {kind}")
    if lent:
        length, data = slots(state.codegen, operand_address(state, part))
        return emit_lent_cursor(state.codegen, source, length, data, kind.heap)
    return emit_owned_cursor(state.codegen, source,
                             OwnedContainer(operand_value(state, part), container, slots))


def _elements(state: FunctionState, rvalue: r.Aggregate, source: r.IterElements) -> ir.Value:
    element = _item_of(state, rvalue)
    return _cursor(state, rvalue, array_source(state.codegen, element), source.container,
                   _elements_slots(source.container), source.lent)


def _map_view(state: FunctionState, rvalue: r.Aggregate, source: r.IterMapView) -> ir.Value:
    if not isinstance(source.container, StructType):
        state.fault(f"a view of a map over {source.container}")
    return _cursor(state, rvalue, bucket_source(state.codegen, source.container, source.view),
                   source.container, bucket_slots, source.lent)


def _range(state: FunctionState, rvalue: r.Aggregate, source: r.IterRange) -> ir.Value:
    first, count = rvalue.parts
    return emit_range_iterator(state.codegen, operand_value(state, first),
                               operand_value(state, count), source.step)


def _new(state: FunctionState, rvalue: r.Aggregate, source: r.IterNew) -> ir.Value:
    method = state.codegen.module.globals.get(source.next_symbol)
    if not isinstance(method, ir.Function):
        state.fault(f"the module declares no function '{source.next_symbol}'")
    return emit_new_iterator(state.codegen, operand_value(state, rvalue.parts[0]), source.value,
                             _item_of(state, rvalue), method,
                             source.receiver in (Convention.INOUT, Convention.IN_GUARANTEED))


def _item_of(state: FunctionState, rvalue: r.Aggregate) -> Type:
    ty = rvalue.kind.ty
    if not isinstance(ty, IteratorType):
        state.fault(f"an iterator value of {ty}")
    return ty.element_type


#: One row for each source of an iterator value.
_SOURCES: dict[type, Callable[[FunctionState, r.Aggregate, Any], ir.Value]] = {
    r.IterElements: _elements,
    r.IterMapView: _map_view,
    r.IterRange: _range,
    r.IterNew: _new,
}


def emit_iterator_value(state: FunctionState, rvalue: r.Aggregate, kind: r.AggIterator) -> ir.Value:
    return _SOURCES[type(kind.source)](state, rvalue, kind.source)
