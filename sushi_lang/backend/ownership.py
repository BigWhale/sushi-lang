"""The deep copy of a value read out of a container, and the `Drop` set of the backend.

`copy_out` is the one deep-clone entry outside the clone engine
(`tests/unit/test_clone_entry_is_copy_out.py`); `copy_out_into` is its form for a
destination slot. `drops_of` is the backend reader of the
`Drop` set. See docs/design/ownership-conventions.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ownership import (
    Type,
)

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


__all__ = ["copy_out", "copy_out_into", "drops_of", "resolver_for"]


def copy_out(codegen: 'LLVMCodegen', value: ir.Value,
             value_type: Optional[Type]) -> ir.Value:
    """Return an independent copy of a value read out of a container it still owns."""
    return _clone(codegen, value, value_type)


def copy_out_into(codegen: 'LLVMCodegen', dest: ir.Value, source: ir.Value,
                  value_type: Type) -> None:
    """`copy_out` of the value at the address `source`, into the slot `dest`.

    A fixed array that moves by address is cloned from address to address and is never
    one first-class value (docs/design/array-representation.md). `dest` holds no value to
    destroy; the two addresses are equal or do not overlap.
    """
    from sushi_lang.backend.expressions.memory import emit_value_clone_into
    emit_value_clone_into(codegen, dest, source, value_type)


def drops_of(codegen: 'LLVMCodegen') -> frozenset:
    """The type names that implement `Drop`, read from the backend's perk table.

    The backend half of ruling R2a. `owns_resource` and `type_class_of` both take this
    set rather than reaching for a registry, so a caller that cannot supply it does not
    compile -- and every backend caller already holds `codegen`.
    """
    return frozenset(codegen.perk_impl_table.by_perk.get("Drop", ()))


def resolver_for(codegen: 'LLVMCodegen'):
    """A `Type -> Type` resolver over the backend's struct and enum tables.

    A `GenericTypeRef` is found by its interned name (#775).
    """
    def resolve(ty):
        name = str(ty) if isinstance(ty, GenericTypeRef) else getattr(ty, "name", None)
        return (codegen.struct_table.by_name.get(name)
                or codegen.enum_table.by_name.get(name)
                or ty)
    return resolve


def _clone(codegen: 'LLVMCodegen', value: ir.Value,
           value_type: Optional[Type]) -> ir.Value:
    """Deep-copy a value so it owns buffers independent of whatever it came from."""
    if value_type is None:
        return value
    from sushi_lang.backend.expressions.memory import emit_value_clone
    if (isinstance(value.type, ir.PointerType)
            and value.type.pointee == codegen.types.ll_type(value_type)):
        value = codegen.builder.load(value, name="consumed_by_value")
    return emit_value_clone(codegen, value, value_type)
