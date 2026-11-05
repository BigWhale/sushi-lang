"""What a built-in container method does to its receiver and its arguments: ONE table.

The borrow pass reads it for CE2412, CE2408 and CE2430. The typecheck pass reads `mutates`
to refuse an in-place method on a constant (CE2096), and `consumes_args` for the convention
of an argument (`argument_conventions`). It sits outside both passes, so the typecheck pass
never imports the borrow pass. The module also holds the
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
_STORES = MethodEffect(consumes_args=True)
_NO_EFFECT = MethodEffect()

# Only the METHOD NAME is matched here. A caller that acts on `consumes_args` checks that
# the receiver is a container, so a user extension called `push` is not swept up.
METHOD_EFFECTS: dict[str, MethodEffect] = {
    "push": _INSERTS,
    "insert": _INSERTS,
    "alloc": _STORES,
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
ARRAY_FAMILY = "array"
LIST_FAMILY = "list"
HASHMAP_FAMILY = "hashmap"
OWN_FAMILY = "own"
EFFECT_FAMILIES = frozenset({ARRAY_FAMILY, LIST_FAMILY, HASHMAP_FAMILY, OWN_FAMILY})

PRIMITIVE_FAMILY = "primitive"
STRING_FAMILY = "string"
RESULT_FAMILY = "result"
MAYBE_FAMILY = "maybe"

# The receiver of a built-in method: each family of `METHOD_TYPE_REGISTRY` but the clone
# family is in exactly one of the four sets below (ruling R-V, docs/design/ir.md 8.3).
# Only reads: no method of the family changes or consumes its receiver.
RECEIVER_READ_FAMILIES = frozenset({PRIMITIVE_FAMILY, STRING_FAMILY})
# The one row of the string family that writes its receiver: the call of the concat
# peephole. Only SLIR lowering makes it, and no program can name it (docs/design/ir.md 8.9).
CONCAT_IN_PLACE = "concat_in_place"
# Consumes: the family has a `nom self` method, so the answer is per method.
CONSUMING_RECEIVER_FAMILIES = frozenset({RESULT_FAMILY, MAYBE_FAMILY})
# The effect families whose group wrote the receiver rule: a method whose row `mutates`
# takes its receiver `Inout`, every other method reads it. The set can only grow.
RULED_EFFECT_FAMILIES = frozenset({ARRAY_FAMILY, LIST_FAMILY, HASHMAP_FAMILY, OWN_FAMILY})
# No rule yet: the set can only shrink.
NO_RECEIVER_RULE_FAMILIES = frozenset({
    "iterator", "foreign_ptr", "array_hash", "derived_hash", "contract", "function",
})
# The derived `clone` of a struct and an enum is in no set: SLIR lowering makes the rvalue
# `Clone` of every `.clone()`, which reads its receiver (docs/design/ir.md 8.3, 8.7).
CLONE_FAMILY = "derived_clone"


# The methods whose answer is a view of what the receiver holds, by family: a container
# get-out (docs/design/ir.md 7.9). The borrow pass reads the receiver as the owner of the
# view, and SLIR lowering reads it for the destination of the answer.
VIEW_ANSWERS: dict[str, frozenset[str]] = {
    ARRAY_FAMILY: frozenset({"get", "first", "last"}),
    LIST_FAMILY: frozenset({"get", "first", "last"}),
    HASHMAP_FAMILY: frozenset({"get"}),
    OWN_FAMILY: frozenset({"get"}),
}

# The arguments of a storing method that only give a position, by family and method: the
# index of `insert` borrows, and only the value is a slot (the `slot` of an array row).
POSITION_ARGUMENTS: dict[tuple[str, str], frozenset[int]] = {
    (ARRAY_FAMILY, "insert"): frozenset({0}),
    (LIST_FAMILY, "insert"): frozenset({0}),
}


def answers_view(method: BuiltinMethod) -> bool:
    """Whether a call of `method` answers a view of what its receiver holds."""
    return method.name in VIEW_ANSWERS.get(method.family, frozenset())


def receiver_convention(method: BuiltinMethod) -> Optional[Convention]:
    """The convention of the receiver of a call of `method`, or None when its family has
    no receiver rule yet: a family of `NO_RECEIVER_RULE_FAMILIES`.

    A family of `RECEIVER_READ_FAMILIES` reads its receiver (`Guaranteed`), but
    `concat_in_place` of the string family writes it (`Inout`). A method of a
    family of `CONSUMING_RECEIVER_FAMILIES` takes its receiver (`Owned`) when it declares
    `nom self`, and reads it otherwise. A method of a family of `RULED_EFFECT_FAMILIES`
    writes its receiver (`Inout`) when its row `mutates`, and reads it otherwise. The clone
    family reads its receiver. Any other family in no set is an internal error.
    """
    if method.family == STRING_FAMILY and method.name == CONCAT_IN_PLACE:
        return Convention.INOUT
    if method.family in RECEIVER_READ_FAMILIES or method.family == CLONE_FAMILY:
        return Convention.GUARANTEED
    if method.family in CONSUMING_RECEIVER_FAMILIES:
        return Convention.OWNED if method.nom_self else Convention.GUARANTEED
    if method.family in RULED_EFFECT_FAMILIES:
        return Convention.INOUT if effect_of(method.name).mutates else Convention.GUARANTEED
    if method.family in NO_RECEIVER_RULE_FAMILIES:
        return None
    raise_internal_error("CE0000", detail=f"the method family '{method.family}' is in no "
                                          f"receiver set of `semantics/method_effects.py`")


def argument_conventions(family: str, method: str, count: int) -> tuple[Convention, ...]:
    """The convention of each of the `count` arguments of a call of `method` of the effect
    family `family`, a method or a static of `BUILTIN_STATICS` (docs/design/ir.md 8.3). A
    method whose row `consumes_args` takes each argument (`Owned`): a container insert, and
    `Own.alloc`, but an argument of `POSITION_ARGUMENTS`. Each other argument is a borrow
    (`Guaranteed`). A family that is not an effect family is an internal error."""
    if family not in EFFECT_FAMILIES:
        raise_internal_error("CE0000", detail=f"the method family '{family}' has no argument "
                                              f"rule in `semantics/method_effects.py`")
    owned = effect_of(method).consumes_args
    positions = POSITION_ARGUMENTS.get((family, method), frozenset())
    return tuple(Convention.OWNED if owned and index not in positions
                 else Convention.GUARANTEED for index in range(count))


def methods_where(flag: str) -> frozenset[str]:
    """Every method name whose effect sets `flag`."""
    return frozenset(name for name, effect in METHOD_EFFECTS.items()
                     if getattr(effect, flag))


MUTATING_METHODS = methods_where("mutates")
CONTAINER_INSERT_METHODS = methods_where("consumes_args")
