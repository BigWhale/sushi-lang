"""Array literal emission for fixed-size arrays."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.ast import ArrayLiteral
from sushi_lang.semantics.typesys import ArrayType
from sushi_lang.backend.types.arrays import runs

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_array_literal(codegen: 'LLVMCodegen', expr: ArrayLiteral) -> ir.Value:
    """Emit array literal as LLVM array constant or initialization.

    The element type is the one the typecheck pass stamped (#889). A missing stamp is a
    gap in the typecheck pass and CE0041, never a guess.
    """
    if not expr.elements:
        raise NotImplementedError("empty array literals not supported yet")

    stamped = expr.resolved_type
    if not isinstance(stamped, ArrayType):
        raise_internal_error("CE0041", type=f"{type(stamped).__name__} on an array literal")
    emitted = runs.emit_runs(codegen, expr.elements, stamped.base_type)
    element_type = runs.element_llvm_type(codegen, emitted)
    # A fixed array's length is part of its TYPE, so it is always readable here: the
    # typecheck pass reported CE2017 or CE2019 for anything else and stopped (Ruling 3).
    length = runs.readable_total(emitted)
    if length is None:
        raise_internal_error("CE0042", type="a fixed array literal with a run-time length")
    array_type = ir.ArrayType(element_type, length)

    # A constant literal still expands here: an initializer holds the values, so there is
    # no loop to keep it short. A run that must be stored goes through the fill instead. A
    # readable RANGE qualifies: its plan carries the values, so they expand the same way.
    expanded = runs.constant_values(emitted, element_type)
    if expanded is not None:
        return ir.Constant(array_type, expanded)

    array_alloca = codegen.memory.entry_alloca(array_type, "array_literal")
    runs.fill_fixed_slot(codegen, array_alloca, emitted, element_type)
    return codegen.builder.load(array_alloca)
