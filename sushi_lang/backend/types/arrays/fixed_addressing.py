"""The one place a fixed-array VALUE becomes an address."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def as_fixed_array_address(codegen: 'LLVMCodegen', value: ir.Value) -> ir.Value:
    """A fixed array as an ADDRESS, spilling its value to a slot if it is a value.

    The fixed twin of `as_array_address`. The array family reads a `T[N]` through a GEP, and
    a borrowed array argument arrives as its value (`xs.extend(row)`). The receiver of a
    method never comes here: the SLIR emitter gives the address of its place
    (docs/design/ir.md 8.6), so a method that changes a `T[N]` writes its owner. A value is
    stored into a slot in the entry block. It is a copy that the method only reads, and it
    owns nothing that this seam frees.
    """
    if isinstance(value.type, ir.PointerType):
        return value
    slot = entry_alloca(codegen.builder, value.type, name="fixed_array_slot")
    codegen.builder.store(value, slot)
    return slot
