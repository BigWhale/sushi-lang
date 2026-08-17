"""The one place a dynamic-array VALUE becomes an address."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def as_array_address(codegen: 'LLVMCodegen', value: ir.Value,
                     array_struct_type: ir.Type | None = None) -> ir.Value:
    """A dynamic array as an ADDRESS, spilling its descriptor to a slot if it is a value.

    `emit_expr` yields a `T[]` as its descriptor by value, like every other type, while the
    array machinery reads a receiver and an index target through a GEP. This is the seam
    between the two, and the reason there is one: the convention used to differ per
    expression -- an inline `from([...])` gave a pointer and a Name gave a value -- so a
    value position took a pointer (#281, #283) and an address position took a value.

    An address that arrives is KEPT. That is what makes a mutating method reach the owner:
    a Name hands over its slot and a field read hands over a GEP into the struct. A value
    can only have come from a temporary, so nobody else can observe the spilled copy.
    """
    if isinstance(value.type, ir.PointerType):
        return value
    slot = codegen.builder.alloca(array_struct_type or value.type, name="array_addr_slot")
    codegen.builder.store(value, slot)
    return slot
