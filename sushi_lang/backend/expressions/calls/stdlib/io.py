"""Call emission for the <io/files> module functions.

Every function -- the path utilities and the descriptor layer alike -- is emitted from
its row in `FILES_SIGNATURES` through the one signature seam (#550). This file used to
hold five if-arms that spelled the parameter types, the Result payload and the arity by
hand, beside a table that spelled the descriptor half a second time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_registry_call
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.io.files_funcs import FILES_SIGNATURES

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_files_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to an io/files module function."""
    require_builder(codegen)

    sig = FILES_SIGNATURES.get(func_name)
    if sig is None:
        raise_internal_error("CE0024", type="io/files", method=func_name)
    return emit_registry_call(codegen, expr, func_name,
                              f"sushi_io_files_{func_name}", sig, to_i1)
