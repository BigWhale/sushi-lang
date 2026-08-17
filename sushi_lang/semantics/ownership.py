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

At an `ADOPT` the source is not consumed -- but the use is still a consuming use, because
the position requires ownership and adopting is how it is satisfied. "Transfer", "handoff"
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
    DynamicArrayType,
    GenericTypeRef,
    ReferenceType,
    Type,
    UnknownType,
    owns_heap,
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
                           # match payload binding, a foreach binding, a peek/poke
                           # parameter, a `let` bound from any of these, and every read
                           # THROUGH a still-live owner -- `s.field`, `own.get()`, and a
                           # container get-out
    FRESH = "fresh"        # nothing owns it yet: a constructor, a call result,
                           # `.clone()`, a literal, a `List.pop()` (which REMOVES the
                           # element, so the container stops owning it)


class TypeClass(Enum):
    """What a value of type `T` owns."""

    PLAIN = "plain"  # owns no heap: i32, bool, f64, a struct of only these
    MOVE = "move"    # owns heap: a `string`, `T[]`, `List@(T)`, `Own@(T)`, `HashMap@(K,V)`,
                     # a function value, or any composite transitively holding one
    #
    # There used to be a third class, COPY, for a value that owns heap but is duplicated
    # rather than transferred -- a `string`, and string-only composites. Phase 9 deleted it:
    # a string now MOVES like every other owning value, except that a binding initialised
    # straight from a literal owns nothing at all and classifies PLAIN (option B, MM.md
    # S0.4). Two classes, one question: does this own heap?


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
    """Classify `T` as PLAIN or MOVE.

    `resolve` maps a named `UnknownType` to its concrete struct/enum. It is a parameter
    rather than a table lookup so this module stays free of both the backend's tables and
    the analyzer's: the borrow checker passes a resolver built from `self.tables`, the
    backend passes its own. Resolving matters -- `owns_heap` answers False for an
    `UnknownType`, so an unresolved owning struct would classify as PLAIN and be aliased.

    Only PLAIN and MOVE exist since Phase 9. `TypeClass.COPY` -- the tier that made a
    string-only composite duplicate rather than transfer -- is gone, and with it the last
    deep copy the compiler inserted on its own. Every deep copy in a Sushi program is now one
    the user wrote as `.clone()`.
    """
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
# The set coincides with the containers that keep their own clone/method paths, so it is
# spelled ONCE (semantics/generics/cloning.py) and aliased here under the name that says
# what this module uses it for.
from sushi_lang.semantics.generics.cloning import (  # noqa: E402
    CONTAINER_PREFIXES as _GET_OUT_PREFIXES,
)


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
