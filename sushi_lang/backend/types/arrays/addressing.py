"""The one place a dynamic-array VALUE becomes an address."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def as_array_address(codegen: 'LLVMCodegen', value: ir.Value,
                     array_struct_type: ir.Type | None = None) -> ir.Value:
    """A dynamic array as an ADDRESS, spilling its descriptor to a slot if it is a value.

    A `T[]` is its descriptor by value, like every other type, while the array machinery
    reads a receiver and an index target through a GEP. This is the seam between the two
    (#281, #283). An address that arrives is KEPT: that is what makes a mutating method
    reach the owner. A value is stored into a slot in the entry block; the owner of the
    value is SLIR's to say (docs/design/ir.md 8.10), so this seam frees nothing.
    """
    if isinstance(value.type, ir.PointerType):
        return value
    slot = entry_alloca(codegen.builder, array_struct_type or value.type, name="array_addr_slot")
    codegen.builder.store(value, slot)
    return slot
