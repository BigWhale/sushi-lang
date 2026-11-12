"""Call emission for the <io/files> module functions.

Every function -- the path utilities and the descriptor layer alike -- is emitted from
its row in `FILES_SIGNATURES` through the one signature seam (#550). A path is a C
string: the row call makes the copy right before the call and frees it right after it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_stdlib_row
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_files_function(codegen: 'LLVMCodegen', func_name: str, symbol: str,
                        args: Sequence[tuple[ir.Value, Type]]) -> ir.Value:
    """Emit a call to an io/files module function, from the values and types of its operands."""
    return emit_stdlib_row(codegen, "io/files", func_name, symbol, args)
