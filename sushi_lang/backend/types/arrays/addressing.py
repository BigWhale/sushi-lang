"""The one place a dynamic-array VALUE becomes an address."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Expr
    from sushi_lang.semantics.typesys import Type


def as_array_address(codegen: 'LLVMCodegen', value: ir.Value,
                     array_struct_type: ir.Type | None = None,
                     source_expr: Optional['Expr'] = None,
                     semantic_type: Optional['Type'] = None) -> ir.Value:
    """A dynamic array as an ADDRESS, spilling its descriptor to a slot if it is a value.

    `emit_expr` yields a `T[]` as its descriptor by value, like every other type, while the
    array machinery reads a receiver and an index target through a GEP. This is the seam
    between the two, and the reason there is one: the convention used to differ per
    expression -- an inline `from([...])` gave a pointer and a Name gave a value -- so a
    value position took a pointer (#281, #283) and an address position took a value.

    An address that arrives is KEPT. That is what makes a mutating method reach the owner:
    a Name hands over its slot and a field read hands over a GEP into the struct.

    A value has no owner yet, and whether it needs one is the question `park_value` answers
    from `source_expr`. Spilling to a bare `alloca` -- which is all this did -- leaked the
    buffer of every unbound array temporary: `give()??[0]`, `from([1, 2]).len()`,
    `a.clone()[0]` (#382). Without the expression the question cannot be asked, so a caller
    that passes none still gets the bare slot.
    """
    if isinstance(value.type, ir.PointerType):
        return value
    if source_expr is not None:
        from sushi_lang.backend.expressions.memory import park_value
        return park_value(codegen, source_expr, value, semantic_type,
                          slot_type=array_struct_type or value.type)
    slot = codegen.builder.alloca(array_struct_type or value.type, name="array_addr_slot")
    codegen.builder.store(value, slot)
    return slot
