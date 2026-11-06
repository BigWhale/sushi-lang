"""The one C-string seam: a `string` argument of a callee that takes `i8*` (docs/design/ir.md 8.6, 8.9).

The callee reads a NUL-terminated copy. The caller makes the copy right before the call and
frees it right after the call, so the copy lives for the call alone. Never marshal inside a
generated body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir

from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_cstr_arg(codegen: LLVMCodegen, value: ir.Value) -> ir.Value:
    """The NUL-terminated copy of the `string` value `value`, in a new block. The caller
    frees it after the call (`free_cstr_args`)."""
    return codegen.runtime.strings.emit_to_cstr(value)


def free_cstr_args(codegen: LLVMCodegen, copies: Sequence[ir.Value]) -> None:
    """Free each copy that `emit_cstr_arg` made for one call."""
    builder = require_builder(codegen)
    for copy in copies:
        builder.call(codegen.get_free_func(), [copy])
