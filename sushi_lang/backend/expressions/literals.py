"""Literal expression emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_int_constant(codegen: 'LLVMCodegen', ty: 'Type', value: int) -> ir.Constant:
    """An integer value at the width of `ty`: the SLIR emitter calls it for an `Int` constant."""
    ll = codegen.types.ll_type(ty)
    mask = (1 << ll.width) - 1
    return ir.Constant(ll, value & mask)


