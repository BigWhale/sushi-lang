"""What a built-in container method does to its receiver and its arguments: ONE table.

The borrow pass reads it for CE2412, CE2408 and CE2430, and the typecheck pass reads
`mutates` to refuse an in-place method on a constant (CE2096). It sits outside both
passes, so the typecheck pass never imports the borrow pass. The module also holds the
partition of the method families by what a method does to its receiver, and the
convention of a receiver (`receiver_convention`, docs/design/ir.md 8.3): the borrow pass
and SLIR lowering read it.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.param_modes import Convention

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.callee import BuiltinMethod


@dataclass(frozen=True)
class MethodEffect:
    """The borrow-pass facts about one built-in method name."""
    mutates: bool = False        # changes or releases what the receiver holds (CE2412, CE2408)
    moves_storage: bool = False  # can reallocate, shrink or free the receiver's storage (#956)
    consumes_args: bool = False  # stores each argument, so each is a consuming use
    bulk_writes: bool = False    # grows the receiver from a borrowed source (CE2430)
    refills: bool = False        # frees each slot, then stores a copy of its argument (CE2430)


_MUTATES = MethodEffect(mutates=True, moves_storage=True)
_WRITES_IN_PLACE = MethodEffect(mutates=True)
_INSERTS = MethodEffect(mutates=True, moves_storage=True, consumes_args=True)
_BULK_WRITES = MethodEffect(mutates=True, moves_storage=True, bulk_writes=True)
_REFILLS = MethodEffect(mutates=True, refills=True)
_NO_EFFECT = MethodEffect()

# Only the METHOD NAME is matched here. A caller that acts on `consumes_args` checks that
# the receiver is a container, so a user extension called `push` is not swept up.
METHOD_EFFECTS: dict[str, MethodEffect] = {
    "push": _INSERTS,
    "insert": _INSERTS,
    "extend": _BULK_WRITES,
    "extend_range": _BULK_WRITES,
    "extend_str": _BULK_WRITES,
    "extend_back": _MUTATES,
    "pop": _MUTATES,
    "remove": _MUTATES,
    "clear": _MUTATES,
    "truncate": _MUTATES,
    "reserve": _MUTATES,
    "shrink_to_fit": _MUTATES,
    "rehash": _MUTATES,
    "destroy": _MUTATES,
    "free": _MUTATES,
    "resize": _MUTATES,
    "fill": _REFILLS,
    "reverse": _WRITES_IN_PLACE,
    "swap": _WRITES_IN_PLACE,
}


def effect_of(method: str) -> MethodEffect:
    """The effect of a method name; a name the table does not carry has none."""
    return METHOD_EFFECTS.get(method, _NO_EFFECT)


# The built-in method families (`METHOD_TYPE_REGISTRY`) whose methods the table describes.
# A row applies to a call only when one of these families answered it: `string.reverse()`
# and a user extension named `push` are other methods with the same name.
EFFECT_FAMILIES = frozenset({"array", "list", "hashmap", "own"})

PRIMITIVE_FAMILY = "primitive"
STRING_FAMILY = "string"
RESULT_FAMILY = "result"
MAYBE_FAMILY = "maybe"

# The receiver of a built-in method: each family of `METHOD_TYPE_REGISTRY` is in exactly
# one of the four sets below (ruling R-V, docs/design/ir.md 8.3).
# Only reads: no method of the family changes or consumes its receiver.
RECEIVER_READ_FAMILIES = frozenset({PRIMITIVE_FAMILY, STRING_FAMILY})
# Consumes: the family has a `nom self` method, so the answer is per method.
CONSUMING_RECEIVER_FAMILIES = frozenset({RESULT_FAMILY, MAYBE_FAMILY})
# No rule yet: the set can only shrink.
NO_RECEIVER_RULE_FAMILIES = frozenset({
    "iterator", "foreign_ptr", "array_hash", "derived_hash", "derived_clone", "contract",
    "function",
})


def receiver_convention(method: BuiltinMethod) -> Optional[Convention]:
    """The convention of the receiver of a call of `method`, or None when its family has
    no receiver rule yet: a family of `EFFECT_FAMILIES` or of `NO_RECEIVER_RULE_FAMILIES`.

    A family of `RECEIVER_READ_FAMILIES` reads its receiver (`Guaranteed`). A method of a
    family of `CONSUMING_RECEIVER_FAMILIES` takes its receiver (`Owned`) when it declares
    `nom self`, and reads it otherwise. A family in no set is an internal error.
    """
    if method.family in RECEIVER_READ_FAMILIES:
        return Convention.GUARANTEED
    if method.family in CONSUMING_RECEIVER_FAMILIES:
        return Convention.OWNED if method.nom_self else Convention.GUARANTEED
    if method.family in EFFECT_FAMILIES | NO_RECEIVER_RULE_FAMILIES:
        return None
    raise_internal_error("CE0000", detail=f"the method family '{method.family}' is in no "
                                          f"receiver set of `semantics/method_effects.py`")


def callee_effect(method: str, family: Optional[str]) -> MethodEffect:
    """The effect of a call whose callee the typecheck pass stamped as `family`."""
    if family not in EFFECT_FAMILIES:
        return _NO_EFFECT
    return effect_of(method)


def methods_where(flag: str) -> frozenset[str]:
    """Every method name whose effect sets `flag`."""
    return frozenset(name for name, effect in METHOD_EFFECTS.items()
                     if getattr(effect, flag))


MUTATING_METHODS = methods_where("mutates")
CONTAINER_INSERT_METHODS = methods_where("consumes_args")
