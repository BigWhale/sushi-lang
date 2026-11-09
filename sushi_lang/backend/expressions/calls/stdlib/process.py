"""Call emission for the <sys/process> module functions, each from its row (#827).

The last operand of `run` is the `string[]` of its variadic tail, which the caller keeps.
`exit` answers `~`, and SLIR lowering ends its block in `Unreachable`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_stdlib_row
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_process_function(codegen: 'LLVMCodegen', func_name: str, symbol: str,
                          args: Sequence[tuple[ir.Value, Type]]) -> ir.Value:
    """Emit a call to a sys/process module function, from the values and types of its
    operands."""
    return emit_stdlib_row(codegen, "sys/process", func_name, symbol, args)
