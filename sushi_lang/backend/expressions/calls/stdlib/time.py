"""Call emission for the <time> module functions.

Every function is emitted from its row in `TIME_SIGNATURES` through the one signature
seam (#827). The generated functions answer a bare value. The clocks are bare, and a
sleep row's `bare_ok` says how the call site builds the Result (`status_result.py`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_stdlib_row
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_time_function(codegen: 'LLVMCodegen', func_name: str, symbol: str,
                       args: Sequence[tuple[ir.Value, Type]]) -> ir.Value:
    """Emit a call to a time module function, from the values and types of its operands."""
    return emit_stdlib_row(codegen, "time", func_name, symbol, args)
