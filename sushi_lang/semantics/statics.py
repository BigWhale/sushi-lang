"""Static methods: a receiver-less method called on a type name (#542).

One namespace sits behind a type's dot (ruling Q1): a name there is a MEMBER of that
type -- a variant, or a static method -- and never both. A local of the same name wins
first, which is #296's rule and not a new one.

This module holds what every pass needs to ASK, so no pass answers it a second way:
which names are type names, and which type a receiver denotes. What a static RESOLVES
to is `passes/types/calls/statics.py`, because that answer needs the method tables.
"""
from __future__ import annotations

from typing import AbstractSet, Optional

from sushi_lang.semantics.typesys import BuiltinType

# Every primitive a receiver position may name. `~` is excluded: it is the blank type
# and has no methods to hang a static on.
BUILTIN_TYPE_NAMES: frozenset[str] = frozenset(
    ty.value for ty in BuiltinType if ty is not BuiltinType.BLANK)


def builtin_type_named(name: str) -> Optional[BuiltinType]:
    """The primitive a name denotes, or None."""
    return BuiltinType(name) if name in BUILTIN_TYPE_NAMES else None


def names_a_type(name: str, *,
                 structs: AbstractSet[str] = frozenset(),
                 enums: AbstractSet[str] = frozenset(),
                 generic_structs: AbstractSet[str] = frozenset(),
                 generic_enums: AbstractSet[str] = frozenset()) -> bool:
    """Whether `name` denotes a TYPE, of any kind a static may be declared on.

    Local-wins is the CALLER's to apply: this asks about the name alone, and a pass
    that has a scope asks whether a local holds the name before it asks this.
    """
    return (name in BUILTIN_TYPE_NAMES
            or name in structs
            or name in enums
            or name in generic_structs
            or name in generic_enums)


# The BUILT-IN statics (ruling R3). They are static methods under the same rule as a
# user's, and they are named here so the general path can DEFER to them: each is
# emitted by its container's own narrow handler, which no user declaration reaches, so
# refusing one as "no such static" would break every `List.new()` in the stdlib.
#
# One table, not a string test per pass. Converging the HANDLERS is a separate change:
# a container static has no `ExtendDef` to resolve, so there is nothing yet to converge
# onto, and #553's lesson says the narrow paths stay until a test proves otherwise.
BUILTIN_STATICS: dict[str, frozenset[str]] = {
    "List": frozenset({"new", "with_capacity"}),
    "HashMap": frozenset({"new"}),
    "Own": frozenset({"alloc"}),
    "f64": frozenset({"from_bits"}),
    "f32": frozenset({"from_bits"}),
}


def is_builtin_static(type_name: Optional[str], method: str) -> bool:
    """Whether `<type_name>.<method>()` is one of the built-in statics."""
    if type_name is None:
        return False
    return method in BUILTIN_STATICS.get(type_name, frozenset())
