"""Call emission for the <time> module functions.

Every function is emitted from its row in `TIME_SIGNATURES` through the one signature
seam (#827). The generated functions answer a bare value, and the row's `bare_ok` says
how the call site builds the Result (`status_result.py`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_registry_call
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.time import TIME_SIGNATURES

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_time_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a time module function."""
    require_builder(codegen)

    sig = TIME_SIGNATURES.get(func_name)
    if sig is None:
        raise_internal_error("CE0024", type="time", method=func_name)
    return emit_registry_call(codegen, expr, func_name, f"sushi_{func_name}", sig, to_i1)
