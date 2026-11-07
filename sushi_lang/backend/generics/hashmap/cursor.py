"""The step of a `HashMapCursor` walk (docs/design/ir.md 8.6, 8.9).

A `cursor_next` row reads the map, goes from the cursor to the next slot that holds an entry,
writes the index after that slot into the cursor, and answers `Some` of the item of its view,
or `None` after the last slot. It is one `linkonce_odr` function for each map type, view and
form of the answer, over the one counted walk (`container_walk.py`), and LLVM inlines it. A
value of an entry that owns stays in its slot: the answer is a view of it, or the address of
the key or the value when the type of the answer holds a pointer.
"""
from __future__ import annotations

from typing import Any

import llvmlite.ir as ir

from sushi_lang.backend.constants import ENTRY_KEY_INDICES, ENTRY_VALUE_INDICES
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend.generics.hashmap.types import ENTRY_OCCUPIED, get_hashmap_field_ptrs
from sushi_lang.backend.generics.hashmap.utils import emit_entry_state_check
from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
from sushi_lang.backend.lifecycle import lifecycle_symbol
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import EnumType, PointerType, StructType, Type


def emit_hashmap_cursor_next(codegen: Any, view: str, address: ir.Value, cursor: ir.Value,
                             map_type: StructType, answer: EnumType) -> ir.Value:
    """The call of the step of `view` over the map at `address` and the cursor at `cursor`."""
    return codegen.builder.call(_step_function(codegen, view, map_type, answer),
                                [address, cursor], name="cursor_next")


def _step_function(codegen: Any, view: str, map_type: StructType,
                   answer: EnumType) -> ir.Function:
    payload = _payload_type(answer)
    form = "address_" if isinstance(payload, PointerType) else ""
    symbol = lifecycle_symbol(f"hashmap_cursor_next_{form}{view}_", map_type)
    found = codegen.module.globals.get(symbol)
    if isinstance(found, ir.Function):
        return found
    signature = ir.FunctionType(codegen.types.ll_type(answer), [
        codegen.types.ll_type(map_type).as_pointer(), codegen.types.i32.as_pointer()])
    fn = ir.Function(codegen.module, signature, name=symbol)
    fn.linkage = "linkonce_odr"
    saved_builder, saved_func = codegen.builder, codegen.func
    codegen.builder = ir.IRBuilder(fn.append_basic_block(name="entry"))
    codegen.func = fn
    try:
        _emit_step(codegen, fn, view, payload)
    finally:
        codegen.builder, codegen.func = saved_builder, saved_func
    return fn


def _payload_type(answer: EnumType) -> Type:
    some = answer.get_variant("Some")
    if some is None or len(some.associated_types) != 1:
        raise_internal_error("CE0000", detail=f"a step of a map answers {answer}")
    return some.associated_types[0]


def _emit_step(codegen: Any, fn: ir.Function, view: str, payload: Type) -> None:
    """The walk of the slots from the cursor to the capacity. The first slot that holds an
    entry returns."""
    builder = codegen.builder
    owner, cursor = fn.args
    fields = get_hashmap_field_ptrs(codegen, owner)
    first = builder.load(cursor, name="first")
    data = builder.gep(builder.load(fields.buckets_data, name="buckets"), [first], name="rest")
    count = builder.sub(builder.load(fields.capacity, name="capacity"), first, name="count")

    def on_slot(entry: ir.Value, index: ir.Value) -> None:
        held = emit_entry_state_check(codegen, entry, ENTRY_OCCUPIED, "is_occupied")
        with builder.if_then(held):
            after = builder.add(builder.add(first, index), ir.Constant(codegen.types.i32, 1),
                                name="after")
            builder.store(after, cursor)
            builder.ret(emit_maybe_some(codegen, payload, _item(codegen, entry, view, payload)))

    emit_container_walk(codegen, data, count, on_slot, prefix="slot")
    builder.ret(emit_maybe_none(codegen, payload))


def _item(codegen: Any, entry: ir.Value, view: str, payload: Type) -> ir.Value:
    """The item of `view` in the slot `entry`: the address of the key or the value in the
    slot, or a shallow copy of the key, the value, or both in one value."""
    builder = codegen.builder
    key = builder.gep(entry, ENTRY_KEY_INDICES, name="key_ptr")
    value = builder.gep(entry, ENTRY_VALUE_INDICES, name="value_ptr")
    if view in ("keys", "values"):
        held = key if view == "keys" else value
        return held if isinstance(payload, PointerType) else builder.load(held, name="item")
    both = ir.Constant(codegen.types.ll_type(payload), ir.Undefined)
    both = builder.insert_value(both, builder.load(key, name="key"), 0, name="item_key")
    return builder.insert_value(both, builder.load(value, name="value"), 1, name="item")
