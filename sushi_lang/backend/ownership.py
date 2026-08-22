"""The seam: the only way a value may be given to a new owner.

`tests/unit/test_consuming_use_coverage.py` fails the build if any other backend module
touches a move-mark primitive. See docs/design/ownership-conventions.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.ast import Name
from sushi_lang.semantics.ownership import (
    ConsumingUse,
    Ownership,
    Provenance,
    Type,
    classify,
    type_class_of,
)

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


__all__ = ["ConsumingUse", "bind", "consume", "copy_out", "relinquish",
           "relinquish_temp", "resolver_for"]


def relinquish_temp(codegen: 'LLVMCodegen', name: str) -> None:
    """Transfer a compiler-SYNTHESIZED temporary to a new owner, by name."""
    da = getattr(codegen, "dynamic_arrays", None)
    if da is not None:
        da.mark_as_moved(name)
    codegen.memory.mark_struct_as_moved(name)


def relinquish(codegen: 'LLVMCodegen', name: str) -> None:
    """State that a local NAMES storage it does not own, so no exit path frees it."""
    da = getattr(codegen, "dynamic_arrays", None)
    if da is not None:
        da.mark_as_moved(name)
    codegen.memory.mark_struct_as_moved(name)


def bind(codegen: 'LLVMCodegen', source, value: ir.Value,
         target_type: Optional[Type]) -> tuple[ir.Value, bool]:
    """Bind a `let`; returns (value to store, whether the caller must register cleanup).

    `consume()` with one answer mapped differently: REJECT means the source is a borrow, so
    the binding owns nothing rather than being CE0129 (#242).
    """
    provenance = _provenance_of(source, ConsumingUse.LET)
    decision = classify(provenance, type_class_of(target_type, resolver_for(codegen)))

    if decision is Ownership.MOVE:
        _mark_moved(codegen, source)
        return value, True
    if decision is Ownership.ADOPT:
        return value, True
    return value, False


def consume(codegen: 'LLVMCodegen', source, value: ir.Value,
            target_type: Optional[Type], use: ConsumingUse) -> ir.Value:
    """Give `value` to a new owner, and return what the caller should store.

    An unstamped source is CE0129: deliberately fatal with no fallback, because a fallback
    that guessed would be a second derivation of the rule this seam exists to centralize.
    """
    provenance = _provenance_of(source, use)
    decision = classify(provenance, type_class_of(target_type, resolver_for(codegen)))

    if decision is Ownership.MOVE:
        _mark_moved(codegen, source)
        return value
    if decision is Ownership.ADOPT:
        return value

    # REJECT is CE2411, which the borrow pass reports before codegen ever runs. Reaching it here
    # means the borrow checker classified the same source differently from this call --
    # impossible while both go through `classify`, so it is a real internal error.
    raise_internal_error("CE0129", use=use.value, node=type(source).__name__)


def copy_out(codegen: 'LLVMCodegen', value: ir.Value,
             value_type: Optional[Type]) -> ir.Value:
    """Return an independent copy of a value read out of a container it still owns."""
    return _clone(codegen, value, value_type)


def resolver_for(codegen: 'LLVMCodegen'):
    """A `Type -> Type` resolver over the backend's struct and enum tables."""
    def resolve(ty):
        name = getattr(ty, "name", None)
        return (codegen.struct_table.by_name.get(name)
                or codegen.enum_table.by_name.get(name)
                or ty)
    return resolve


def _provenance_of(source, use: ConsumingUse) -> Provenance:
    """The `Provenance` the borrow pass stamped on `source`, or CE0129 if there is none."""
    provenance = getattr(source, "ownership_provenance", None)
    if provenance is None:
        raise_internal_error("CE0129", use=use.value, node=type(source).__name__)
    return provenance


def _mark_moved(codegen: 'LLVMCodegen', source) -> None:
    """Record that the source no longer owns the value, so scope exit skips it."""
    if not isinstance(source, Name):
        return
    codegen.memory.mark_struct_as_moved(source.id)
    da = getattr(codegen, "dynamic_arrays", None)
    if da is not None:
        da.mark_as_moved(source.id)


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
