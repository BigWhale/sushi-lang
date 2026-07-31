"""The seam: the only way a value may be given to a new owner.

`semantics/ownership.py` holds the rule. This module applies it. Every position that
takes ownership of a value calls `consume()`, which reads the `Provenance` Pass 3 stamped
on the source expression, asks the shared `classify()` table what that means for the
target type, and performs the answer.

Why the split, restated here because this is the half people edit:

    The backend does not know provenance and cannot work it out. It has LLVM values and
    cleanup registries, and the question it can answer -- "is this name registered for
    cleanup?" (`is_owned_local`) -- is not the question that matters. Those two coincide
    for a `let` local and a fresh temporary and diverge for exactly one thing: a binding.
    That divergence is the (BORROWED, MOVE) cell, and it is where every shipped bug in
    this family lived.

    So provenance arrives stamped from semantics, and this module supplies the resolved
    target type, which semantics frequently does not have.

**Nothing may bypass this module.** The ownership-transferring primitives -- the two move
marks and the deep clone -- are reached only from here, and
`tests/unit/test_consuming_use_coverage.py` fails the build when any other backend module
references them. A shared helper that callers may decline to call is not an authority;
that is the difference between this and the four point fixes that preceded it.
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


__all__ = ["ConsumingUse", "consume", "copy_out", "resolver_for"]


def consume(codegen: 'LLVMCodegen', source, value: ir.Value,
            target_type: Optional[Type], use: ConsumingUse) -> ir.Value:
    """Give `value` to a new owner, and return what the caller should store.

    Args:
        codegen: The LLVM codegen instance.
        source: The source AST expression. Carries the `Provenance` Pass 3 stamped on it.
        value: The already-emitted SSA value.
        target_type: The semantic type of the position receiving the value.
        use: Which of the eleven positions this is.

    Returns:
        The value to store: the original for MOVE/ADOPT, a deep copy for COPY.

    Raises:
        CE0129 when the source carries no ownership decision. This is deliberately fatal
        and deliberately has no fallback -- a fallback that guessed would be a twelfth
        derivation of the rule, which is the thing this seam exists to make impossible.
    """
    provenance = _provenance_of(source, use)
    decision = classify(provenance, type_class_of(target_type, resolver_for(codegen)))

    if decision is Ownership.MOVE:
        _mark_moved(codegen, source)
        return value
    if decision is Ownership.COPY:
        return _clone(codegen, value, target_type)
    if decision is Ownership.ADOPT:
        return value

    # REJECT is CE2411, which Pass 3 reports before codegen ever runs. Reaching it here
    # means the borrow checker classified the same source differently from this call --
    # impossible while both go through `classify`, so it is a real internal error.
    raise_internal_error("CE0129", use=use.value, node=type(source).__name__)


def copy_out(codegen: 'LLVMCodegen', value: ir.Value,
             value_type: Optional[Type]) -> ir.Value:
    """Return an independent copy of a value read out of a container it still owns.

    The reader-side counterpart of `consume`. An array / `List` / `HashMap` get-out, and
    the auto-derived `.clone()`, all hand back a value the container keeps owning, so
    each must detach with its own deep copy.

    Deliberately takes no `ConsumingUse`-style enum: a read through a still-live owner
    ALWAYS copies. There is no decision to record, so an enum here would carry no
    information -- unlike `consume`, where the whole point is that the answer varies.

    Routed through this module for one reason: it keeps the deep clone reachable from
    exactly one place, so the no-bypass gate can be a simple grep rather than an
    allowlist that drifts.
    """
    return _clone(codegen, value, value_type)


def resolver_for(codegen: 'LLVMCodegen'):
    """A `Type -> Type` resolver over the backend's struct and enum tables.

    `type_class_of` needs it because `type_moves_by_value` answers False for an
    `UnknownType`: an owning struct still named by its declaration would otherwise
    classify as owning nothing, and every consuming use of it would alias.

    Public because the closure environment gates ask the same question about a captured
    field that `consume` asks about the capture itself. A second resolver would be a
    second answer.
    """
    def resolve(ty):
        name = getattr(ty, "name", None)
        return (codegen.struct_table.by_name.get(name)
                or codegen.enum_table.by_name.get(name)
                or ty)
    return resolve


# --- Internals. Everything below is private to the seam. ---------------------------

def _provenance_of(source, use: ConsumingUse) -> Provenance:
    """The `Provenance` Pass 3 stamped on `source`, or CE0129 if there is none."""
    provenance = getattr(source, "ownership_provenance", None)
    if provenance is None:
        raise_internal_error("CE0129", use=use.value, node=type(source).__name__)
    return provenance


def _mark_moved(codegen: 'LLVMCodegen', source) -> None:
    """Record that the source no longer owns the value, so scope exit skips it.

    Only a bare `Name` has an owner to mark. A `MemberAccess` never reaches here (it is
    THROUGH_OWNER, which copies) and a temporary has no binding at all.

    Both registries are told, because a name may be tracked in either: the dynamic-array
    registry keys on its descriptor's alloca and the general one on the innermost local
    slot. Both ultimately call `codegen.moves.mark(slot)`, so telling both is idempotent
    and telling only one is how a kind of owning value gets missed.
    """
    if not isinstance(source, Name):
        return
    codegen.memory.mark_struct_as_moved(source.id)
    da = getattr(codegen, "dynamic_arrays", None)
    if da is not None:
        da.mark_as_moved(source.id)


def _clone(codegen: 'LLVMCodegen', value: ir.Value,
           value_type: Optional[Type]) -> ir.Value:
    """Deep-copy a value so it owns buffers independent of whatever it came from.

    Accepts a value OR a pointer to one. An owning value does not reach every position in
    the same shape: a `T[]` call argument is still the array-struct POINTER when the
    decision is made (the dispatcher normalizes pointer arguments to values only after),
    and an enum-constructor payload arrives the same way. `emit_value_clone` is strictly
    value-in/value-out, so load first and hand back the cloned VALUE -- the later
    normalization is a no-op on a value, and `cast_for_param` accepts it.
    """
    if value_type is None:
        return value
    from sushi_lang.backend.expressions.memory import emit_value_clone
    if (isinstance(value.type, ir.PointerType)
            and value.type.pointee == codegen.types.ll_type(value_type)):
        value = codegen.builder.load(value, name="consumed_by_value")
    return emit_value_clone(codegen, value, value_type)
