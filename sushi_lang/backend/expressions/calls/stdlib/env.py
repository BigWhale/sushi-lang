"""Call emission for the <sys/env> module functions.

Every function is emitted from its row in `ENV_SIGNATURES` through the one signature
seam (#827). A key and a value are C strings: the row call makes the copy right before
the call and frees it right after it. `setenv` answers a bare status, and the call builds
its Result (`status_result.py`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_stdlib_row
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_env_function(codegen: 'LLVMCodegen', func_name: str, symbol: str,
                      args: Sequence[tuple[ir.Value, Type]]) -> ir.Value:
    """Emit a call to a sys/env module function, from the values and types of its operands."""
    return emit_stdlib_row(codegen, "sys/env", func_name, symbol, args)
