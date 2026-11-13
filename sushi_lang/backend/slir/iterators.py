"""The value of an iterator from its `Aggregate`: one row for each source of the closed table
(docs/design/ir.md 8.7). The row builds the state and supplies the three functions
(`backend/runtime/iterators.py`), and it decides nothing: `heap` is the answer of `borrow`.
"""
from __future__ import annotations

from typing import Any, Callable

from llvmlite import ir

from sushi_lang.backend.generics.hashmap.methods.iterators import bucket_slots, bucket_source
from sushi_lang.backend.generics.list.types import list_slots
from sushi_lang.backend.runtime import iterator_adapters as adapters
from sushi_lang.backend.runtime.iterators import (
    CursorSource, OwnedContainer, array_source, emit_lent_cursor, emit_new_iterator,
    emit_owned_cursor, emit_range_iterator)
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import (
    operand_address, operand_value, type_of_operand, type_of_place)
from sushi_lang.backend.types.arrays.methods.iterators import descriptor_slots, fixed_array_slots
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import BuiltinMethod
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.typesys import (
    ArrayType, DynamicArrayType, FunctionType, IteratorType, StructType, Type)

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
    return emit_range_iterator(state.codegen, _item_of(state, rvalue),
                               operand_value(state, first), operand_value(state, count),
                               source.step)


def _new(state: FunctionState, rvalue: r.Aggregate, source: r.IterNew) -> ir.Value:
    method = state.codegen.module.globals.get(source.next_symbol)
    if not isinstance(method, ir.Function):
        state.fault(f"the module declares no function '{source.next_symbol}'")
    return emit_new_iterator(state.codegen, operand_value(state, rvalue.parts[0]), source.value,
                             _item_of(state, rvalue), method,
                             source.receiver in (Convention.INOUT, Convention.IN_GUARANTEED))


def _parts(state: FunctionState, rvalue: r.Aggregate, count: int) -> list[ir.Value]:
    if len(rvalue.parts) != count:
        state.fault(f"an adapter value with {len(rvalue.parts)} parts, not {count}")
    return [operand_value(state, part) for part in rvalue.parts]


def _result(state: FunctionState, rvalue: r.Aggregate) -> IteratorType:
    ty = rvalue.kind.ty
    if not isinstance(ty, IteratorType):
        state.fault(f"an iterator value of {ty}")
    return ty


def _heap(rvalue: r.Aggregate) -> bool:
    return isinstance(rvalue.kind, r.AggIterator) and rvalue.kind.heap


def _map(state: FunctionState, rvalue: r.Aggregate, source: r.IterMap) -> ir.Value:
    inner, function = _parts(state, rvalue, 2)
    return adapters.emit_map(state.codegen, inner, function, source.function, source.source,
                             _result(state, rvalue), _heap(rvalue))


def _filter(state: FunctionState, rvalue: r.Aggregate, source: r.IterFilter) -> ir.Value:
    inner, predicate = _parts(state, rvalue, 2)
    return adapters.emit_filter(state.codegen, inner, predicate, source.function,
                                source.source, _result(state, rvalue), _heap(rvalue))


def _enumerate(state: FunctionState, rvalue: r.Aggregate, source: r.IterEnumerate) -> ir.Value:
    inner, = _parts(state, rvalue, 1)
    return adapters.emit_enumerate(state.codegen, inner, source.source,
                                   _result(state, rvalue), _heap(rvalue))


def _zip(state: FunctionState, rvalue: r.Aggregate, source: r.IterZip) -> ir.Value:
    inner, other = _parts(state, rvalue, 2)
    return adapters.emit_zip(state.codegen, inner, other, source.source, source.other,
                             _result(state, rvalue), _heap(rvalue))


def _clone_each(state: FunctionState, rvalue: r.Aggregate, source: r.IterCloneEach) -> ir.Value:
    inner, = _parts(state, rvalue, 1)
    return adapters.emit_clone_each(state.codegen, inner, source.source,
                                    _result(state, rvalue), _heap(rvalue))


def _item_of(state: FunctionState, rvalue: r.Aggregate) -> Type:
    return _result(state, rvalue).element_type


#: One row for each source of an iterator value.
_SOURCES: dict[type, Callable[[FunctionState, r.Aggregate, Any], ir.Value]] = {
    r.IterElements: _elements,
    r.IterMapView: _map_view,
    r.IterRange: _range,
    r.IterNew: _new,
    r.IterMap: _map,
    r.IterFilter: _filter,
    r.IterEnumerate: _enumerate,
    r.IterZip: _zip,
    r.IterCloneEach: _clone_each,
}


def emit_iterator_value(state: FunctionState, rvalue: r.Aggregate, kind: r.AggIterator) -> ir.Value:
    return _SOURCES[type(kind.source)](state, rvalue, kind.source)


def _function_of(state: FunctionState, operand: p.Operand) -> FunctionType:
    ty = type_of_operand(state, operand)
    if not isinstance(ty, FunctionType):
        state.fault(f"the function operand of a consumer has the type {ty}")
    return ty


def emit_consumer(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
    """A consumer (`fold`, `partition`, `collect`): the iterator `Owned` first, then each
    argument `Owned`. The consumer walks the iterator to its end here and destroys it
    (docs/design/ir.md 8.7)."""
    if not statement.args:
        state.fault(f"the call of '{method.name}' has no receiver")
    operands = [operand for operand, _ in statement.args]
    receiver = type_of_operand(state, operands[0])
    if not isinstance(receiver, IteratorType):
        state.fault(f"the receiver of '{method.name}' has the type {receiver}")
    values = [operand_value(state, operand) for operand in operands]
    answer = type_of_place(state, statement.dest)
    codegen = state.codegen
    if method.name == "collect" and len(values) == 1:
        return adapters.emit_collect(codegen, values[0], receiver, answer)
    if method.name == "partition" and len(values) == 2:
        return adapters.emit_partition(codegen, values[0], receiver, values[1],
                                       _function_of(state, operands[1]), answer)
    if method.name == "fold" and len(values) == 3:
        return adapters.emit_fold(codegen, values[0], receiver, values[1], values[2],
                                  _function_of(state, operands[2]), answer)
    state.fault(f"no consumer '{method.name}' with {len(values) - 1} arguments")
