"""Array literal emission for fixed-size arrays."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import ArrayLiteral

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_array_literal(codegen: 'LLVMCodegen', expr: ArrayLiteral) -> ir.Value:
    """Emit array literal as LLVM array constant or initialization."""
    if not expr.elements:
        raise NotImplementedError("empty array literals not supported yet")

    element_values = [codegen.expressions.emit_expr(elem) for elem in expr.elements]

    element_type = element_values[0].type
    array_size = len(element_values)

    array_type = ir.ArrayType(element_type, array_size)

    if all(isinstance(val, ir.Constant) for val in element_values):
        return ir.Constant(array_type, element_values)

    array_alloca = codegen.alloca_builder.alloca(array_type, name="array_literal")

    zero = ir.Constant(codegen.i32, 0)
    for i, elem_value in enumerate(element_values):
        index = ir.Constant(codegen.i32, i)
        elem_ptr = codegen.builder.gep(array_alloca, [zero, index])
        codegen.builder.store(elem_value, elem_ptr)

    return codegen.builder.load(array_alloca)
