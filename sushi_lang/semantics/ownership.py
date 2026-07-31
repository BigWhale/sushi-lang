"""One authority for every consuming use.

`docs/design/move-semantics.md` section 3 states a rule -- at a position that takes
ownership, a bare owned value moves, a value read through a still-live owner is copied,
and a fresh temporary is stored as-is -- and until this module existed there was no
function implementing it. Eleven backend positions each re-derived it inline and no two
derivations agreed, which is why the same bug was fixed four times at four different
positions (#238, #250, #256, #277) and stayed live at eight more.

The vocabulary is Swift's, because it is the decomposition that survives the case which
breaks the others (see `docs/design/ownership-conventions.md` section 2):

    A CONSUMING USE is a position that requires ownership of a value.
    The OWNERSHIP CONVENTION at that use is how a given source satisfies it.

At a `COPY` the source is not consumed -- but the use is still a consuming use, because
the position requires ownership and copying is how it is satisfied. "Transfer", "handoff"
and "move" are all false in that case; "consuming use" is not.

Split of responsibilities, and why it is not a second derivation of the rule:

- **Semantics** computes `Provenance` and stamps it on the source AST node. Only semantics
  can: it takes scopes, binding kinds and `borrow_state` to tell an owned local from a
  match binding, and the backend has none of those (it has been approximating with
  `is_owned_local`, which answers a different question -- "is this registered for
  cleanup?").
- **The backend** supplies the resolved target type at the position, which semantics
  frequently does not have.
- **`classify()` below is the only place the rule itself lives.** Both sides call it. The
  inputs differ because each side holds a different half of them; the decision cannot
  disagree with itself, because there is one table.

This module is ir-free and must stay that way: `semantics` never imports `backend`
(Tier 4.1).
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    GenericTypeRef,
    ReferenceType,
    StructType,
    Type,
    UnknownType,
    type_moves_by_value,
)


class ConsumingUse(Enum):
    """Every position that takes ownership of a value. A CLOSED set.

    Closedness is the property that fixes the recurring bug, not the naming. Before this
    enum nobody could answer "what are all the positions?": #250's triage said two, its
    own fix found five, #277 says one, the 2026-07-30 audit found eleven. The set was
    rediscovered empirically every time. An enum makes it impossible to add a twelfth
    without declaring it, and makes coverage assertable
    (`tests/unit/test_consuming_use_coverage.py`).
    """

    CALL_ARG = "call_arg"                # f(x), constructor calls, indirect calls, bloom
    LET = "let"                          # let T x = <source>
    REBIND = "rebind"                    # x := <source>
    FIELD_ASSIGN = "field_assign"        # obj.field := <source>
    STRUCT_FIELD = "struct_field"        # S(field: <source>)
    ENUM_PAYLOAD = "enum_payload"        # E.Variant(<source>), incl. Result.Ok / Maybe.Some
    ARRAY_ELEMENT = "array_element"      # from([<source>, ...]) and [<source>, ...]
    CONTAINER_INSERT = "container_insert"  # List.push/.insert, HashMap.insert (key AND value)
    RETURN = "return"                    # return Result.Ok(<source>)
    CAPTURE = "capture"                  # a lambda's captured environment slot
    OWN_ALLOC = "own_alloc"              # Own.alloc(<source>)


class Provenance(Enum):
    """Where the value at a consuming use came from.

    The half only semantics can compute.
    """

    OWNED = "owned"        # a registered owner in this scope: a `let` local or a
                           # by-value parameter
    BORROWED = "borrowed"  # names storage owned elsewhere, for a SHORTER lifetime: a
                           # match payload binding, a foreach binding, a &peek/&poke
                           # parameter, a `let` bound from any of these, and every read
                           # THROUGH a still-live owner -- `s.field`, `own.get()`, and a
                           # container get-out
    FRESH = "fresh"        # nothing owns it yet: a constructor, a call result,
                           # `.clone()`, a literal, a `List.pop()` (which REMOVES the
                           # element, so the container stops owning it)


class TypeClass(Enum):
    """What a value of type `T` owns."""

    PLAIN = "plain"  # owns no heap: i32, bool, f64, a struct of only these
    COPY = "copy"    # owns heap, but the policy is to duplicate rather than transfer:
                     # `string`, and string-only or plain+string composites. Rust's `Copy`
                     # tier, derived structurally rather than opted into -- a string is a
                     # fat pointer with a runtime `owned` bit and Sushi deliberately keeps
                     # it a copy type (docs/design/string-representation.md)
    MOVE = "move"    # transitively contains a `T[]`, `List@(T)`, `Own@(T)`, `HashMap@(K,V)`
                     # or a capturing closure


class Ownership(Enum):
    """How the source satisfies the position's requirement for ownership."""

    MOVE = "move"      # the source owned it; mark the source moved, store as-is
    COPY = "copy"      # the source keeps living and keeps owning; store a deep copy
    ADOPT = "adopt"    # nothing owned it; store as-is
    REJECT = "reject"  # the source may not be consumed at all -- CE2411


# The classification table, `docs/design/ownership-conventions.md` section 4.3.
#
# The single cell every shipped bug in this family got wrong is (BORROWED, MOVE): #238
# fixed it at three positions, #250 at five, #256 at six, #277 reports it at one more.
# Per section 8 it is not a code-generation question at all -- consuming a borrowed
# binding of an owning type is rejected, and `.clone()` is the escape.
#
# There used to be a fourth provenance, THROUGH_OWNER, for a read through a still-live
# owner -- `s.field`, `own.get()`, a container get-out. It COPIED where BORROWED rejects,
# and the asymmetry had one reason: a user who could not bind a borrow had no escape from
# a rejection, so every `s.field` would have needed a `.clone()`. #242 supplies the escape,
# so the two rows are now the same row and the compiler inserts no deep copy at a read.
# Every deep copy in a Sushi program is one the user wrote as `.clone()`.
_TABLE: dict[tuple[Provenance, TypeClass], Ownership] = {
    (Provenance.OWNED,    TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.OWNED,    TypeClass.COPY):  Ownership.COPY,
    (Provenance.OWNED,    TypeClass.MOVE):  Ownership.MOVE,

    (Provenance.BORROWED, TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.BORROWED, TypeClass.COPY):  Ownership.COPY,
    (Provenance.BORROWED, TypeClass.MOVE):  Ownership.REJECT,

    (Provenance.FRESH,    TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.FRESH,    TypeClass.COPY):  Ownership.ADOPT,
    (Provenance.FRESH,    TypeClass.MOVE):  Ownership.ADOPT,
}


def classify(provenance: Provenance, type_class: TypeClass) -> Ownership:
    """The rule. Total over the grid, pure, and the only implementation of it.

    Every consuming use in the compiler reaches its decision through this function --
    that is what makes it one authority rather than eleven. Unit-tested cell by cell in
    `tests/unit/test_ownership_table.py`, which is possible only because the decision is
    now a value instead of being fused into eleven emitters.
    """
    return _TABLE[(provenance, type_class)]


# --- Type classification -----------------------------------------------------------

def _IDENTITY(t: Type) -> Type:
    """The default resolver: a caller with no type tables resolves nothing."""
    return t


def type_class_of(ty: Optional[Type], resolve: Callable[[Type], Type] = _IDENTITY) -> TypeClass:
    """Classify `T` as PLAIN / COPY / MOVE.

    `resolve` maps a named `UnknownType` to its concrete struct/enum. It is a parameter
    rather than a table lookup so this module stays free of both the backend's tables and
    the analyzer's: the borrow checker passes a resolver built from `self.tables`, the
    backend passes its own. Resolving matters -- `type_moves_by_value` answers False for
    an `UnknownType`, so an unresolved owning struct would classify as PLAIN and be
    aliased.
    """
    if ty is None:
        return TypeClass.PLAIN

    # A reference is a borrow, never a value that owns anything.
    if isinstance(ty, ReferenceType):
        return TypeClass.PLAIN

    resolved = ty
    if isinstance(ty, UnknownType):
        # A resolver that MISSES must leave the type alone, not blank it: `None` reads as
        # PLAIN downstream, so a miss would silently reclassify an owning value as owning
        # nothing -- the exact failure mode this function exists to prevent.
        resolved = resolve(ty) or ty

    # The resolver is threaded INTO the walk, not just applied at the top: the name that
    # is still unresolved is usually nested (a `Buffer[2]`'s element, a field), and a
    # top-level-only resolve reports such a type as owning nothing.
    if type_moves_by_value(resolved, resolve=resolve):
        return TypeClass.MOVE
    if _contains_string(resolved, resolve, set()):
        return TypeClass.COPY
    return TypeClass.PLAIN


def _contains_string(ty: Optional[Type], resolve: Callable[[Type], Type],
                     seen: set[str]) -> bool:
    """Does a value of this type transitively hold a `string`?

    The ir-free counterpart of the backend's `needs_cleanup` for the non-move half: a
    string is heap-backed, so a composite holding one must be deep-copied rather than
    aliased even though it does not move. Mirrors `type_moves_by_value`'s recursion,
    including its cycle guard -- a self-referential type is finite by construction
    (CE2095 rejects a by-value cycle), so revisiting a name means "no new answer here".
    """
    if ty is None:
        return False
    if isinstance(ty, UnknownType):
        ty = resolve(ty)
        if isinstance(ty, UnknownType):
            return False
    if ty == BuiltinType.STRING:
        return True
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return _contains_string(ty.base_type, resolve, seen)
    if isinstance(ty, StructType):
        if ty.name in seen:
            return False
        seen.add(ty.name)
        return any(_contains_string(ft, resolve, seen) for _, ft in ty.fields)
    if isinstance(ty, EnumType):
        if ty.name in seen:
            return False
        seen.add(ty.name)
        return any(_contains_string(at, resolve, seen)
                   for variant in ty.variants for at in variant.associated_types)
    return False


def is_own_type(ty: Optional[Type]) -> bool:
    """Is `ty` an `Own@(T)`?

    The ir-free twin of the backend's `is_own_get_call` receiver test. `Own` is a smart
    pointer, not a collection: `get()` is a dereference that hands back a bare `T` without
    copying. That made it the one container whose get-out was a view while every other
    one deep-copied at the read, which is what #256 was. Since #242 every container reads
    the same way, so this is now one arm of `is_get_out_container` rather than a rule of
    its own.
    """
    if ty is None:
        return False
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if isinstance(ty, GenericTypeRef):
        return ty.base_name == "Own"
    name = getattr(ty, "name", None)
    return isinstance(name, str) and name.startswith("Own<")


# The generic containers whose `.get()` reads out of storage the receiver keeps. The
# interned names carry `<...>`, never `@(...)` -- see `semantics/generics/type_display.py`.
_GET_OUT_PREFIXES = ("Own<", "List<", "HashMap<")


def is_get_out_container(ty: Optional[Type]) -> bool:
    """Does `.get()` on a receiver of this type return a VIEW of what the receiver owns?

    True for an array, a `List@(T)`, a `HashMap@(K, V)` and an `Own@(T)`. Each keeps the
    element and still frees it, so what `get()` hands back is a borrow.

    Until #242 every container except `Own` deep-copied at the read, so the answer was
    "only `Own`" and this predicate was `is_own_type`. Deleting the reader-side copies
    made all four the same, and this is the one place that says so. Keyed on the TYPE, not
    on the method name: a user extension method that happens to be called `get` is not a
    container read, and classifying it as one would report a false CE2411.
    """
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
