"""Call emission for the <random> module functions, each from its row (#827)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_registry_call
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.random import RANDOM_SIGNATURES

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_random_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a random module function."""
    require_builder(codegen)

    sig = RANDOM_SIGNATURES.get(func_name)
    if sig is None:
        raise_internal_error("CE0024", type="random", method=func_name)
    return emit_registry_call(codegen, expr, func_name, f"sushi_{func_name}", sig, to_i1)
