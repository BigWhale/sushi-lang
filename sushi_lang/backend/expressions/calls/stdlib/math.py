"""Call emission for the <math> module functions, each from its row (#827).

`abs`, `min` and `max` are a family with one row per argument type. The row is picked
from the SEMANTIC type of the first argument, because an LLVM integer carries no sign:
`min` over two u8 must call `sushi_min_u8`, never the signed `sushi_min_i8` (#817).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import emit_registry_call
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.math import MATH_FAMILIES, MATH_SIGNATURES, family_row

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_math_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a math module function."""
    require_builder(codegen)

    if func_name in MATH_FAMILIES and expr.args:
        from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type

        arg_type = infer_expr_semantic_type(codegen, expr.args[0])
        sig = family_row(func_name, arg_type)
        symbol = f"sushi_{func_name}_{arg_type}"
    else:
        sig = MATH_SIGNATURES.get(func_name)
        symbol = f"sushi_{func_name}"
    if sig is None:
        raise_internal_error("CE0024", type="math", method=func_name)
    return emit_registry_call(codegen, expr, func_name, symbol, sig, to_i1)
