"""Call emission for the <net/socket> module functions, each from its row (#550).

The declared LLVM signature must byte-match what the generator in `sushi_stdlib/src/net`
emitted, which is why both sides build their Result types through `get_result_type`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_stdlib_row
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_net_function(codegen: 'LLVMCodegen', func_name: str, symbol: str,
                      args: Sequence[tuple[ir.Value, Type]]) -> ir.Value:
    """Emit a call to a net/socket module function, from the values and types of its
    operands."""
    return emit_stdlib_row(codegen, "net/socket", func_name, symbol, args)
