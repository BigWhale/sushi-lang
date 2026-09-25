"""What a built-in container method does to its receiver and its arguments: ONE table.

The borrow pass reads it for CE2412, CE2408 and CE2430, and the typecheck pass reads
`mutates` to refuse an in-place method on a constant (CE2096). It sits outside both
passes, so the typecheck pass never imports the borrow pass.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class MethodEffect:
    """The borrow-pass facts about one built-in method name."""
    mutates: bool = False        # changes or releases what the receiver holds (CE2412, CE2408)
    consumes_args: bool = False  # stores each argument, so each is a consuming use
    bulk_writes: bool = False    # grows the receiver from a borrowed source (CE2430)
    refills: bool = False        # frees each slot, then stores a copy of its argument (CE2430)


_MUTATES = MethodEffect(mutates=True)
_INSERTS = MethodEffect(mutates=True, consumes_args=True)
_BULK_WRITES = MethodEffect(mutates=True, bulk_writes=True)
_REFILLS = MethodEffect(mutates=True, refills=True)
_NO_EFFECT = MethodEffect()

# Only the METHOD NAME is matched here. A caller that acts on `consumes_args` checks that
# the receiver is a container, so a user extension called `push` is not swept up.
METHOD_EFFECTS: dict[str, MethodEffect] = {
    "push": _INSERTS,
    "insert": _INSERTS,
    "extend": _BULK_WRITES,
    "extend_range": _BULK_WRITES,
    "pop": _MUTATES,
    "remove": _MUTATES,
    "clear": _MUTATES,
    "truncate": _MUTATES,
    "reserve": _MUTATES,
    "shrink_to_fit": _MUTATES,
    "rehash": _MUTATES,
    "destroy": _MUTATES,
    "free": _MUTATES,
    "fill": _REFILLS,
    "reverse": _MUTATES,
}


def effect_of(method: str) -> MethodEffect:
    """The effect of a method name; a name the table does not carry has none."""
    return METHOD_EFFECTS.get(method, _NO_EFFECT)


def methods_where(flag: str) -> frozenset[str]:
    """Every method name whose effect sets `flag`."""
    return frozenset(name for name, effect in METHOD_EFFECTS.items()
                     if getattr(effect, flag))


MUTATING_METHODS = methods_where("mutates")
CONTAINER_INSERT_METHODS = methods_where("consumes_args")
BULK_WRITE_METHODS = methods_where("bulk_writes")
