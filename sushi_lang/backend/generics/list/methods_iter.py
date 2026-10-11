"""The `iter()` producer of a `List@(T)` (#1259)."""

from typing import Any
import llvmlite.ir as ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
from .types import get_list_len_ptr, get_list_data_ptr, extract_element_type
from sushi_lang.internals.errors import raise_internal_error


def emit_list_iter(
    codegen: Any,
    call: MethodCall,
    list_value: ir.Value,
    list_type: StructType
) -> ir.Value:
    """`l.iter()`: the cursor is the length and the data of the List."""
    from sushi_lang.backend.runtime.iterators import (
        OwnedContainer, array_source, emit_cursor_iterator)

    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    element_semantic_type = extract_element_type(list_type, codegen)
    address = list_value
    if not isinstance(list_value.type, ir.PointerType):
        from sushi_lang.backend.memory.allocas import entry_alloca
        address = entry_alloca(codegen.builder, list_value.type, name="list_receiver")
        codegen.builder.store(list_value, address)
    length, data = list_slots(codegen, address)

    def owned() -> OwnedContainer:
        from sushi_lang.backend.expressions.calls.dispatcher import consume_receiver
        value = consume_receiver(codegen, call, codegen.builder.load(address, name="list"))
        return OwnedContainer(value=value, semantic_type=list_type, slots=list_slots)

    return emit_cursor_iterator(codegen, call, array_source(codegen, element_semantic_type),
                                length, data, owned)


def list_slots(codegen: Any, list_ptr: ir.Value) -> tuple[ir.Value, ir.Value]:
    """The length and the data of a List at an address."""
    b = codegen.builder
    length = b.load(get_list_len_ptr(b, list_ptr), name="list_len")
    data = b.load(get_list_data_ptr(b, list_ptr), name="list_data")
    return length, data
