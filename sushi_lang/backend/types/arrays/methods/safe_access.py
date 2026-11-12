"""Safe array element access returning Maybe<T>."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.backend import gep_utils
from sushi_lang.backend.types.arrays.bounds import emit_checked_maybe, emit_checked_maybe_into

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_array_get_maybe(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    count: ir.Value,
    index_value: ir.Value,
    element_type: 'Type',
    dest: Optional[ir.Value] = None,
) -> Optional[ir.Value]:
    """`.get()` on either array kind: `Maybe.Some(data_ptr[index])`, or `Maybe.None`.

    `.get()` READS. It does not detach (#242): the array keeps the element and still
    frees it, so the `Maybe.Some(...)` carries a BORROW. The borrow pass classifies it
    BORROWED, a `let` of it binds without owning, and a position that takes ownership
    rejects it (CE2411). `.pop()` is the one that still moves, because it removes the element.

    With `dest`, the `Maybe` is written into that slot and the answer is None: the bytes of
    the element are copied from its address, with no load of the element.
    """
    def element_address() -> ir.Value:
        return gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")

    def read_element() -> ir.Value:
        return codegen.builder.load(element_address(), name="element")

    if dest is not None:
        emit_checked_maybe_into(codegen, index_value, count, element_type, element_address,
                                dest)
        return None
    return emit_checked_maybe(codegen, index_value, count, element_type, read_element)
