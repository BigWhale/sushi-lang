"""LLVM emission for the auto-derived hash of a container that holds one type.

A container's backing FIELDS are the wrong thing to hash: `List@(T).data` and
`Own@(T).value` are raw pointers, and the hash of an address says nothing about the
value at it. Two lists with equal elements would answer two hashes, and the backend
could not read the pointer at all -- `Own@(i32).hash()` answered CE0052 and
`List@(i32).hash()` answered CE0042 (#628). Both emitters here read what the container
HOLDS, through the same `emit_element_hash` an array element uses.

`HashMap@(K, V)` has no emitter here on purpose. The semantic walk refuses it, so
nothing reaches this module for a map: its buckets carry a state for each slot and the
slot order is not the entry order, so it needs a fold that this walk cannot give.
"""

from typing import Any

import llvmlite.ir as ir

from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend.generics.list.types import get_list_data_ptr, get_list_len_ptr
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.types.arrays.methods.hashing import emit_element_hash
from sushi_lang.backend.types.hash_utils import emit_fnv1a_combine, emit_fnv1a_init
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory


def _held_type(container_type: StructType, position: int) -> Type:
    """The type argument a container holds, read from the type and not from its name."""
    held = container_type.generic_args or ()
    if position >= len(held):
        raise_internal_error("CE0049", generic=str(container_type.generic_base),
                             name=container_type.name)
    return held[position]


def _as_address(codegen: Any, value: ir.Value, llvm_type: ir.Type, name: str) -> ir.Value:
    """A receiver arrives as an address from a method call and as a VALUE from a field.

    The struct hash walks a loaded struct, so no field of it has an address. A hash only
    READS, so spilling the value to a slot is sound -- the same split the array emitter
    draws.
    """
    if isinstance(value.type, ir.PointerType):
        return value

    slot = entry_alloca(codegen.builder, llvm_type, name=name)
    codegen.builder.store(value, slot)
    return slot


def _make_list_hash_emitter(list_type: Type) -> Any:
    """Build the hash() emitter for one `List@(T)`: its elements, then its length."""
    def emitter(codegen: Any, call: Any, receiver_value: ir.Value,
                receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        if call.args:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        u64 = ir.IntType(INT64_BIT_WIDTH)
        element_type = _held_type(list_type, 0)

        list_ptr = _as_address(codegen, receiver_value, receiver_type, "list_hash_temp")
        length = builder.load(get_list_len_ptr(builder, list_ptr), name="list_hash_len")
        data = builder.load(get_list_data_ptr(builder, list_ptr), name="list_hash_data")

        accumulator = entry_alloca(builder, u64, name="list_hash_acc")
        builder.store(emit_fnv1a_init(codegen), accumulator)

        def on_element(element_ptr: ir.Value, _index: ir.Value) -> None:
            element = codegen.builder.load(element_ptr, name="list_hash_element")
            element_hash = emit_element_hash(codegen, element, element_type)
            current = codegen.builder.load(accumulator)
            codegen.builder.store(
                emit_fnv1a_combine(codegen, current, element_hash), accumulator)

        # An empty list has a null data pointer, so the walk must not read it.
        emit_container_walk(codegen, data, length, on_element,
                            null_guard=True, prefix="list_hash")

        # The length too: without it a list of one zero and a list of two hash alike.
        final = codegen.builder.load(accumulator)
        return emit_fnv1a_combine(codegen, final,
                                  codegen.builder.zext(length, u64, name="list_hash_len64"))

    return emitter


def _make_own_hash_emitter(own_type: Type) -> Any:
    """Build the hash() emitter for one `Own@(T)`: the payload, never the address.

    Two allocations of one value must hash alike, or the hash means nothing to a reader
    who only sees values.
    """
    def emitter(codegen: Any, call: Any, receiver_value: ir.Value,
                receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        if call.args:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        payload_type = _held_type(own_type, 0)

        own_ptr = _as_address(codegen, receiver_value, receiver_type, "own_hash_temp")
        value_ptr = builder.load(
            builder.gep(own_ptr, [ir.Constant(codegen.types.i32, 0),
                                  ir.Constant(codegen.types.i32, 0)],
                        name="own_hash_value_ptr"),
            name="own_hash_value")
        payload = builder.load(value_ptr, name="own_hash_payload")

        return emit_fnv1a_combine(codegen, emit_fnv1a_init(codegen),
                                  emit_element_hash(codegen, payload, payload_type))

    return emitter


# Every kind `CONTAINER_HASH_KINDS` names must have an emitter here, or a container the
# semantic walk accepts reaches the backend with nothing to run it.
# `tests/unit/test_container_hashability_seam.py` is the gate.
EMITTER_FACTORIES = {
    "list": _make_list_hash_emitter,
    "own": _make_own_hash_emitter,
}

for _kind, _factory in EMITTER_FACTORIES.items():
    register_hash_emitter_factory(_kind, _factory)
