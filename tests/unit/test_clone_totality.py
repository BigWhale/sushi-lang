"""`.clone()` must exist on every type that can need it."""
from __future__ import annotations

import pytest

from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
from sushi_lang.semantics.ownership import TypeClass, type_class_of
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    DynamicArrayType as DynArr,
    EnumType,
    FunctionType,
    StructType,
)

# No type in this gate's corpus implements `Drop`; the resource
# half of the predicate is `tests/unit/test_cleanup_predicates_agree.py`.
NO_DROPS: frozenset = frozenset()


def _fn(captures=None) -> FunctionType:
    """A `fn(i32) -> i32`. `captures=None` is the common case AND the owning one."""
    return FunctionType(
        param_types=(BuiltinType.I32,),
        ok_type=BuiltinType.I32,
        err_type=BuiltinType.I32,
        captures=captures,
    )


def _list(elem="i32") -> StructType:
    return StructType(name=f"List<{elem}>", fields=())


def _own(elem="i32") -> StructType:
    return StructType(name=f"Own<{elem}>", fields=())


# Every type family a value can have at a consuming use. Named so a failure says which.
REPRESENTATIVE_TYPES: list[tuple[str, object]] = [
    ("i32", BuiltinType.I32),
    ("i64", BuiltinType.I64),
    ("u8", BuiltinType.U8),
    ("f32", BuiltinType.F32),
    ("f64", BuiltinType.F64),
    ("bool", BuiltinType.BOOL),
    ("string", BuiltinType.STRING),
    ("i32[]", DynArr(base_type=BuiltinType.I32)),
    ("string[]", DynArr(base_type=BuiltinType.STRING)),
    ("i32[3]", ArrayType(base_type=BuiltinType.I32, size=3)),
    ("string[2]", ArrayType(base_type=BuiltinType.STRING, size=2)),
    ("i32[][2]", ArrayType(base_type=DynArr(base_type=BuiltinType.I32), size=2)),
    ("List<i32>", _list()),
    ("Own<i32>", _own()),
    ("fn(i32) -> i32", _fn()),
    ("fn(i32) -> i32 (non-capturing)", _fn(captures=())),
]


# Families the two clauses deliberately do not cover. A silent skip is what E1 and E2 were;
# each entry states why the property is vacuous or wrong for it.
#
# * `ptr`   -- CE5011 gives it no methods at all, and CE5012 forbids it as a generic type
#              argument outside Result/Maybe. It owns nothing, so it can never be MOVE.
#              Both clauses are vacuous for it BY CONSTRUCTION, not by exception.
# * `~`, stdin/stdout/stderr/file
#           -- `~` is not a value a user binds. The io handles are a builtin `FILE*`
#              until Phase 5 of HANDLES.md turns them into a `File` struct; when it does,
#              they become RESOURCE types and the entry below is the rule that covers
#              them. Do not widen this reason back to "not a value a user binds".
# * Iterator<T>
#           -- a transient view over storage someone else owns; Rust's `Iterator: Clone` is
#              opt-in for the same reason. If an iterator ever becomes ownable, this is the
#              exemption to revisit.
EXEMPT_REASONS: dict[str, str] = {
    "ptr": "no methods (CE5011); not a generic type argument (CE5012); owns nothing",
    "blank": "not a value a user binds",
    "io handles": "a RESOURCE type: no clone by design (CE2431), and no valid argument "
                  "to a generic that clones -- the escape is .share(), and CE2411 names it",
    "Iterator<T>": "a transient view; ownership belongs to what it iterates",
}


# Clause 1 -- the escape must exist
#
# Only a MOVE type reaches REJECT, so clause 1 is filtered rather than skipped: a skip means
# "this environment cannot run it", and a type that is PLAIN by construction is not that.
# `test_the_gate_can_actually_see_types` keeps the filter from silently emptying the list.
MOVE_TYPES = [(n, t) for n, t in REPRESENTATIVE_TYPES if type_class_of(t, NO_DROPS) is TypeClass.MOVE]


@pytest.mark.parametrize("name,ty", MOVE_TYPES, ids=[n for n, _ in MOVE_TYPES])
def test_a_move_type_has_a_clone(name, ty):
    """If consuming a borrow of `T` is CE2411, `T.clone()` must exist."""
    assert builtin_method_exists(ty, "clone"), (
        f"{name} is a MOVE type, so consuming a borrow of it is CE2411 -- and CE2411's help "
        f"text tells the user to call .clone(), which does not exist on it. That is a "
        f"rejection with no escape. This exact hole has already stopped two phases."
    )


# Clause 2 -- monomorphization

# A type parameter is substituted with a concrete type and the ONE body is compiled for each.
# So `.clone()` must exist on every type that can be a type argument, not only on the owning
# ones -- `fn first@(T)(T[] arr) T` needs `elem.clone()` for `T = string` and is instantiated
# at `T = i32` as well, which is why a primitive has a clone that does nothing.
@pytest.mark.parametrize("name,ty", REPRESENTATIVE_TYPES, ids=[n for n, _ in REPRESENTATIVE_TYPES])
def test_a_generic_type_argument_has_a_clone(name, ty):
    """One monomorphized body must satisfy every instantiation of its type parameter."""
    assert builtin_method_exists(ty, "clone"), (
        f"{name} can be a generic type argument, so a body written as `x.clone()` for an "
        f"owning instantiation fails to compile when it is instantiated at {name}. The "
        f"escape must survive monomorphization -- Rust makes `Copy: Clone` for this reason."
    )


# The auto-derived pair, which needs the analyzer to have run

def test_user_struct_and_enum_carry_a_clone(analyze):
    """The derive pass registers clone from SEMANTICS, so the registry answer is import-order safe."""
    analyze("""
struct Bag:
    i32[] items

enum Holder:
    Full(i32[])
    Empty

fn main() i32:
    let Bag b = Bag(from([1, 2, 3]))
    let Holder h = Holder.Full(from([4]))
    println(b.items.len())
    return Result.Ok(0)
""")
    bag = StructType(name="Bag", fields=())
    holder = EnumType(name="Holder", variants=())
    assert builtin_method_exists(bag, "clone"), "the derive pass must auto-derive a struct clone"
    assert builtin_method_exists(holder, "clone"), "the derive pass must auto-derive an enum clone"


def test_an_owning_user_struct_is_move_and_therefore_needs_its_clone(analyze):
    """The two clauses meet: a struct with a `T[]` field is MOVE, so clause 1 binds to it."""
    analyze("""
struct Bag:
    i32[] items

fn main() i32:
    let Bag b = Bag(from([1, 2, 3]))
    println(b.items.len())
    return Result.Ok(0)
""")
    bag = StructType(name="Bag", fields=(("items", DynamicArrayType(base_type=BuiltinType.I32)),))
    assert type_class_of(bag, NO_DROPS) is TypeClass.MOVE
    assert builtin_method_exists(StructType(name="Bag", fields=()), "clone")


# The former known hole, now closed
#
# `test_hashmap_clone_is_a_known_hole_owned_by_phase_10` lived here. It asserted that
# `HashMap@(K, V)` had NO clone and that a HashMap was not yet a MOVE type, and it carried its
# own closing instruction: "Add the clone, then delete this test -- it will already be
# failing, because the assertion below is that the hole EXISTS."
#
# Phase 9 absorbed that Phase 10 item and added `HashMap.clone()`, so the test was failing by
# design and is gone. The property it guarded is not lost: clause 1 below now binds to
# HashMap for real, rather than passing over it vacuously.
#
# The clone EMITTER was never the hole. `_clone_hashmap_value` has been the destructor's
# symmetric partner since issue #181; only the method was missing.


def test_hashmap_carries_a_clone():
    """The closed hole, asserted from the other side."""
    hashmap = StructType(name="HashMap<i32, i32>", fields=())
    assert builtin_method_exists(hashmap, "clone"), (
        "HashMap.clone() is the only escape from CE2411 for a borrowed HashMap; it must exist"
    )


def test_the_gate_can_actually_see_types():
    """Guard against the whole file passing because every case skipped or the list emptied."""
    assert len(REPRESENTATIVE_TYPES) >= 12
    moving = [n for n, t in REPRESENTATIVE_TYPES if type_class_of(t, NO_DROPS) is TypeClass.MOVE]
    assert len(moving) >= 4, f"clause 1 must actually bind to something; it bound to {moving}"
