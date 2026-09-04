"""Call emission for the <net/socket> module functions.

Every primitive is emitted from its row in `SOCKET_SIGNATURES` through the one
signature seam (#550). The declared LLVM signature must byte-match what the generator
in `sushi_stdlib/src/net` emitted, which is why both sides build their Result types
through `get_result_type` rather than spelling the struct out.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_registry_call
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.net.socket_funcs import SOCKET_SIGNATURES

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_net_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a net/socket module function."""
    require_builder(codegen)

    sig = SOCKET_SIGNATURES.get(func_name)
    if sig is None:
        raise_internal_error("CE0055", name=f"net/socket/{func_name}")
    return emit_registry_call(codegen, expr, func_name,
                              f"sushi_net_{func_name}", sig, to_i1)
