"""One authority for every consuming use: `classify()` is the only place the rule lives.

Semantics stamps `Provenance` on the source node (only it has scopes and `borrow_state`);
the backend supplies the resolved target type. Both then call `classify()`, so the two
sides cannot disagree. Must stay ir-free -- `semantics` never imports `backend`.
See docs/design/ownership-conventions.md sections 1 and 2.
"""
from __future__ import annotations

from enum import Enum
from typing import AbstractSet, Callable, Optional

from sushi_lang.semantics.typesys import (
    ArrayType,
    DynamicArrayType,
    GenericTypeRef,
    ReferenceType,
    Type,
    UnknownType,
    owns_resource,
)


class ConsumingUse(Enum):
    """Every position that takes ownership of a value. A CLOSED set."""

    CALL_ARG = "call_arg"                # f(x), constructor calls, indirect calls, bloom
    LET = "let"                          # let T x = <source>
    REBIND = "rebind"                    # x := <source>
    FIELD_ASSIGN = "field_assign"        # obj.field := <source>
    STRUCT_FIELD = "struct_field"        # S(field: <source>)
    ENUM_PAYLOAD = "enum_payload"        # E.Variant(<source>), incl. Result.Ok / Maybe.Some
    ARRAY_ELEMENT = "array_element"      # from([<source>, ...]) and [<source>, ...]
    ELEMENT_ASSIGN = "element_assign"    # arr[i] := <source>
    CONTAINER_INSERT = "container_insert"  # List.push/.insert, HashMap.insert (key AND value)
    RETURN = "return"                    # return Result.Ok(<source>)
    CAPTURE = "capture"                  # a lambda's captured environment slot
    OWN_ALLOC = "own_alloc"              # Own.alloc(<source>)
    MATCH_SCRUTINEE = "match_scrutinee"  # match nom <source>: -- ruling R11
    RECEIVER = "receiver"                # h.close() on a `nom self` method -- ruling R25


class Provenance(Enum):
    """Where the value at a consuming use came from."""

    OWNED = "owned"        # a registered owner in this scope: a `let` local or a
    BORROWED = "borrowed"  # names storage owned elsewhere, for a SHORTER lifetime: a
                           # match/foreach binding, a peek/poke parameter, a `let` bound
                           # from one, or any read through a still-live owner
    FRESH = "fresh"        # nothing owns it yet: a constructor, a call result,


class TypeClass(Enum):
    """What a value of type `T` owns."""

    PLAIN = "plain"  # owns no heap: i32, bool, f64, a struct of only these
    MOVE = "move"    # owns heap: a `string`, `T[]`, `List@(T)`, `Own@(T)`, `HashMap@(K,V)`,
                     # a function value, or any composite transitively holding one
    #
    # Two classes, one question: does this own heap? A third class, COPY, was deleted --
    # a `string` MOVES now, except that a literal-bound binding classifies PLAIN (option B).


class Ownership(Enum):
    """How the source satisfies the position's requirement for ownership."""

    MOVE = "move"      # the source owned it; mark the source moved, store as-is
    ADOPT = "adopt"    # nothing owned it; store as-is
    REJECT = "reject"  # the source may not be consumed at all -- CE2411


# The classification table, `docs/design/ownership-conventions.md` section 4.3.
#
# (BORROWED, MOVE) is the cell every shipped bug in this family got wrong (#238, #250,
# #256, #277): consuming a borrow of an owning type is REJECTED, and `.clone()` is the
# escape. A fourth provenance and a COPY column were both deleted -- which is what makes
# `.clone()` the only deep copy in a Sushi program.
_TABLE: dict[tuple[Provenance, TypeClass], Ownership] = {
    (Provenance.OWNED,    TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.OWNED,    TypeClass.MOVE):  Ownership.MOVE,

    (Provenance.BORROWED, TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.BORROWED, TypeClass.MOVE):  Ownership.REJECT,

    (Provenance.FRESH,    TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.FRESH,    TypeClass.MOVE):  Ownership.ADOPT,
}


def classify(provenance: Provenance, type_class: TypeClass) -> Ownership:
    """The rule. Total over the grid, pure, and the only implementation of it."""
    return _TABLE[(provenance, type_class)]


def _IDENTITY(t: Type) -> Type:
    """The default resolver: a caller with no type tables resolves nothing."""
    return t


def type_class_of(ty: Optional[Type], drops: AbstractSet[str],
                  resolve: Callable[[Type], Type] = _IDENTITY) -> TypeClass:
    """Classify `T` as PLAIN or MOVE.

    `drops` names the types that implement `Drop`, and it is required for the reason
    `owns_resource` states: a missing answer classifies every handle PLAIN, and PLAIN
    means copied, dropped twice and never moved.
    """
    if ty is None:
        return TypeClass.PLAIN

    # A reference classifies as its REFERENT: the borrow lives in the PROVENANCE, and the
    # question here is the other half -- does the value own heap? Answering PLAIN made the
    # (BORROWED, MOVE) cell unreachable through a reference type, so the checker adopted
    # silently while the backend rejected: #301, #310, #311.
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type

    resolved = ty
    if isinstance(ty, UnknownType):
        # A resolver that MISSES must leave the type alone, not blank it: `None` reads as
        # PLAIN downstream, so a miss would silently reclassify an owning value as owning
        # nothing -- the exact failure mode this function exists to prevent.
        resolved = resolve(ty) or ty

    # The resolver is threaded INTO the walk, not just applied at the top: the name that
    # is still unresolved is usually nested (a `Buffer[2]`'s element, a field), and a
    # top-level-only resolve reports such a type as owning nothing.
    if owns_resource(resolved, drops, resolve=resolve):
        return TypeClass.MOVE
    return TypeClass.PLAIN


def is_own_type(ty: Optional[Type]) -> bool:
    """Is `ty` an `Own@(T)`?"""
    if ty is None:
        return False
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if isinstance(ty, GenericTypeRef):
        return ty.base_name == "Own"
    name = getattr(ty, "name", None)
    return isinstance(name, str) and name.startswith("Own<")


# Containers whose `.get()` reads out of storage the receiver keeps. Interned names carry
# `<...>`, never `@(...)`. Spelled ONCE in semantics/generics/cloning.py and aliased here.
from sushi_lang.semantics.generics.cloning import (  # noqa: E402
    CONTAINER_PREFIXES as _GET_OUT_PREFIXES,
)


def is_get_out_container(ty: Optional[Type]) -> bool:
    """Does `.get()` on a receiver of this type return a VIEW of what the receiver owns?"""
    if ty is None:
        return False
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return True
    if isinstance(ty, GenericTypeRef):
        return ty.base_name in ("Own", "List", "HashMap")
    name = getattr(ty, "name", None)
    return isinstance(name, str) and name.startswith(_GET_OUT_PREFIXES)
