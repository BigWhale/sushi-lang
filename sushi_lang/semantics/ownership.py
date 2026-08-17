"""One authority for every consuming use: `classify()` is the only place the rule lives.

Semantics stamps `Provenance` on the source node (only it has scopes and `borrow_state`);
the backend supplies the resolved target type. Both then call `classify()`, so the two
sides cannot disagree. Must stay ir-free -- `semantics` never imports `backend`.
See docs/design/ownership-conventions.md sections 1 and 2.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from sushi_lang.semantics.typesys import (
    ArrayType,
    DynamicArrayType,
    GenericTypeRef,
    ReferenceType,
    Type,
    UnknownType,
    owns_heap,
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
    CONTAINER_INSERT = "container_insert"  # List.push/.insert, HashMap.insert (key AND value)
    RETURN = "return"                    # return Result.Ok(<source>)
    CAPTURE = "capture"                  # a lambda's captured environment slot
    OWN_ALLOC = "own_alloc"              # Own.alloc(<source>)


class Provenance(Enum):
    """Where the value at a consuming use came from."""

    OWNED = "owned"        # a registered owner in this scope: a `let` local or a
    BORROWED = "borrowed"  # names storage owned elsewhere, for a SHORTER lifetime: a
                           # match payload binding, a foreach binding, a peek/poke
                           # parameter, a `let` bound from any of these, and every read
                           # THROUGH a still-live owner -- `s.field`, `own.get()`, and a
                           # container get-out
    FRESH = "fresh"        # nothing owns it yet: a constructor, a call result,


class TypeClass(Enum):
    """What a value of type `T` owns."""

    PLAIN = "plain"  # owns no heap: i32, bool, f64, a struct of only these
    MOVE = "move"    # owns heap: a `string`, `T[]`, `List@(T)`, `Own@(T)`, `HashMap@(K,V)`,
                     # a function value, or any composite transitively holding one
    #
    # There used to be a third class, COPY, for a value that owns heap but is duplicated
    # rather than transferred -- a `string`, and string-only composites. Phase 9 deleted it:
    # a string now MOVES like every other owning value, except that a binding initialised
    # straight from a literal owns nothing at all and classifies PLAIN (option B,
    # docs/design/ownership-conventions.md). Two classes, one question: does this own heap?


class Ownership(Enum):
    """How the source satisfies the position's requirement for ownership."""

    MOVE = "move"      # the source owned it; mark the source moved, store as-is
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
# Phase 9 made this 3x2. The COPY column was the compiler inserting a deep copy of its own
# accord; deleting it is what makes `.clone()` the only deep copy in a Sushi program.
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


def type_class_of(ty: Optional[Type], resolve: Callable[[Type], Type] = _IDENTITY) -> TypeClass:
    """Classify `T` as PLAIN or MOVE."""
    if ty is None:
        return TypeClass.PLAIN

    # A reference classifies as its REFERENT. The borrow is in the PROVENANCE, which
    # `_name_provenance` already answers BORROWED for a reference-typed name, and the
    # question this function asks is the other half: does the value own heap?
    #
    # Short-circuiting to PLAIN here answered the ownership question with the borrow
    # question, and that made the (BORROWED, MOVE) cell -- the cell that says "you
    # cannot consume a borrow" -- UNREACHABLE through a reference type. So every
    # consuming use of a reference parameter landed in (BORROWED, PLAIN) = ADOPT, which
    # the checker performed silently while the backend classified the same transfer from
    # the TARGET type and answered REJECT: #301's CE0129, #310's compile-clean double
    # free, #311's ref-to-ref rebind. One question, two answers -- the thing this module
    # exists to make impossible.
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
    if owns_heap(resolved, resolve=resolve):
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


# The generic containers whose `.get()` reads out of storage the receiver keeps. The
# interned names carry `<...>`, never `@(...)` -- see `semantics/generics/type_display.py`.
# The set coincides with the containers that keep their own clone/method paths, so it is
# spelled ONCE (semantics/generics/cloning.py) and aliased here under the name that says
# what this module uses it for.
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
