"""Call emission for the <sys/env> module functions.

Every function is emitted from its row in `ENV_SIGNATURES` through the one signature
seam (#827). A key and a value are C strings, marshalled by `emit_cstr_arg` and freed
at scope exit like an FFI argument (#292).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_registry_call
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.sys.env import ENV_SIGNATURES

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_env_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a sys/env module function."""
    require_builder(codegen)

    sig = ENV_SIGNATURES.get(func_name)
    if sig is None:
        raise_internal_error("CE0024", type="sys/env", method=func_name)
    return emit_registry_call(codegen, expr, func_name, f"sushi_{func_name}", sig, to_i1)
