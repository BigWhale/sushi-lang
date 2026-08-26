"""The one place a fixed-array RECEIVER becomes an address."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Expr
    from sushi_lang.semantics.typesys import Type


def as_fixed_array_address(codegen: 'LLVMCodegen', expr: 'Expr', value: ir.Value,
                           array_ir_type: ir.ArrayType,
                           semantic_type: Optional['Type'] = None, *,
                           writable: bool) -> ir.Value:
    """A fixed array as an ADDRESS, resolved from the AST rather than from the value.

    The fixed twin of `as_array_address`, and it exists for the same reason: the rule used
    to live at every site that wanted an address -- two in the dispatcher, three in the
    iterators, two in the hashing, and one each in `get` and `clone` -- and each of the nine
    carried its own version. A receiver that was not a bare `Name` fell to an `alloca` of a
    COPY, so `b.slots.fill(9)` filled the copy and the owner kept its old elements. No
    diagnostic was possible, because that store is legal (#480).

    Resolved from the AST, and never from `value`: `emit_member_access` gives a fixed-array
    field BY VALUE, unlike a dynamic one, and it must keep doing so. A `T[]` already lives
    with a value/pointer duality that `as_array_address` normalises, while `[N x T]` is a
    value in every other position -- an assignment, an argument, a hash. Returning a pointer
    from the field read would change all of them.

    `try_get_struct_alloca` answers a `Name`, a nested `MemberAccess` chain, an
    `IndexAccess` and a reference parameter, which is every receiver that names storage.

    WRITABLE is the whole of the read/write split. A read may spill a value that names no
    storage; a write may not, and there is no fallback for it. That is what keeps a store
    out of `.rodata`: a constant resolves to its global for a read and to NOTHING for a
    write, so no such binary can be built even if CE2096 were bypassed. The other
    unwritable receivers have their own diagnostics -- CE2408, CE2414, CE2421, CE2422,
    CE2426, CE2429 -- so reaching the fatal arm means one of them did not fire. Same
    treatment `backend/ownership.py` gives a consuming use with no decision (CE0129).
    """
    from sushi_lang.backend.expressions.structs import try_get_struct_alloca
    from sushi_lang.semantics.ast import Name

    # An address that ARRIVES is kept: a `peek` / `poke` fixed array reaches the dispatcher
    # as `[N x T]*`, because `_deref_borrowed_receiver` loads through a borrow only when the
    # referent is a BuiltinType, and an array is not one.
    if isinstance(value.type, ir.PointerType):
        return value

    address = try_get_struct_alloca(codegen, expr)
    if address is not None:
        return address

    if writable:
        raise_internal_error("CE0132", node=type(expr).__name__)

    if isinstance(expr, Name):
        from sushi_lang.backend.expressions.names import resolve_name_slot
        slot = resolve_name_slot(codegen, expr.id)
        if slot is not None:
            return slot

    # A value that names no storage. `park_value` and not a bare `alloca`, so an owning
    # temporary gets an owner rather than leaking its buffer (#382).
    from sushi_lang.backend.expressions.memory import park_value
    return park_value(codegen, expr, value, semantic_type, slot_type=array_ir_type)
