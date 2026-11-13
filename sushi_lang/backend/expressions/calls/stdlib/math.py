"""Call emission for the <math> module functions, each from its row (#827).

`abs`, `min` and `max` are families, one row per argument type. The row is picked
from the SEMANTIC type of the first argument, because an LLVM integer carries no sign:
`min` over two u8 must call `sushi_min_u8`, never the signed `sushi_min_i8` (#817).
`stdlib_row` picks the row and `stdlib_symbol` names it (`semantics/stdlib_registry.py`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_stdlib_row
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_math_function(codegen: 'LLVMCodegen', func_name: str, symbol: str,
                       args: Sequence[tuple[ir.Value, Type]]) -> ir.Value:
    """Emit a call to a math module function, from the values and types of its operands."""
    return emit_stdlib_row(codegen, "math", func_name, symbol, args)
