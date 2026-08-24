"""Array literal emission for fixed-size arrays."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import ArrayLiteral
from sushi_lang.backend.types.arrays import runs

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_array_literal(codegen: 'LLVMCodegen', expr: ArrayLiteral) -> ir.Value:
    """Emit array literal as LLVM array constant or initialization."""
    if not expr.elements:
        raise NotImplementedError("empty array literals not supported yet")

    emitted = runs.emit_runs(codegen, expr.elements, None)
    element_type = emitted[0].value.type
    array_type = ir.ArrayType(element_type, runs.total_elements(emitted))

    # A constant literal still expands here: an initializer holds the values, so there is
    # no loop to keep it short. A run that must be stored goes through the fill instead.
    if all(isinstance(run.value, ir.Constant) for run in emitted):
        expanded = [run.value for run in emitted for _ in range(run.count)]
        return ir.Constant(array_type, expanded)

    array_alloca = codegen.alloca_builder.alloca(array_type, name="array_literal")
    runs.fill_fixed_slot(codegen, array_alloca, emitted, element_type)
    return codegen.builder.load(array_alloca)
