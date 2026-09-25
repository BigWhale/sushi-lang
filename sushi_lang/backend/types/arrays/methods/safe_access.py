"""Safe array element access returning Maybe<T>."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend import gep_utils
from sushi_lang.backend.types.arrays.bounds import emit_checked_maybe

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_array_get_maybe(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    count: ir.Value,
    index_value: ir.Value,
    element_type: 'Type',
) -> ir.Value:
    """`.get()` on either array kind: `Maybe.Some(data_ptr[index])`, or `Maybe.None`.

    `.get()` READS. It does not detach (#242): the array keeps the element and still
    frees it, so the `Maybe.Some(...)` carries a BORROW. The borrow pass classifies it
    BORROWED, a `let` of it binds without owning, and a position that takes ownership
    rejects it (CE2411). `.pop()` is the one that still moves, because it removes the element.
    """
    def read_element() -> ir.Value:
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
        return codegen.builder.load(element_ptr, name="element")

    return emit_checked_maybe(codegen, index_value, count, element_type, read_element)
