"""`.clone()` must exist on every type that can need it.

`.clone()` is the ONLY escape from CE2411. `classify(BORROWED, MOVE)` is `REJECT`
(semantics/ownership.py) and the diagnostic's own help text is "clone it to take an
independent value". So a type that can be MOVE and carries no `.clone()` is a rejection
with no way out: the compiler tells the user to do something the language does not offer.

**That hole has been found twice by a sweep and never by a test.** Phase 7 found it for
`List<T>`, `Own<T>` and `string` (MM.md finding A5). Phase 8 found it again for the
primitives and for fixed arrays (finding F1). Both times a phase stopped mid-flight and the
phase order had to change. This file is the gate that makes the next one a red CI run.

Same spirit as tests/unit/test_builtin_method_seam.py and
tests/unit/test_borrow_dispatch_is_total.py: assert the property, not an instance of it.
Both clauses read the REAL predicates -- `type_class_of` from the ownership table and
`builtin_method_exists` from the method seam -- so neither can drift from what the compiler
actually does.
"""
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
    EnumVariantInfo,
    FunctionType,
    StructType,
)


def _fn(captures=None) -> FunctionType:
    """A `fn(i32) -> i32`. `captures=None` is the common case AND the owning one.

    `FunctionType.__eq__` excludes captures from type identity, so a value arriving
    through a declared type -- a struct field, a `List` element, a parameter -- has already
    lost it. `is_owning_type` reads `None` as owning for exactly that reason (MM.md A1).
    """
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
#           -- not value types a user binds, passes and drops.
# * Iterator<T>
#           -- a transient view over storage someone else owns; Rust's `Iterator: Clone` is
#              opt-in for the same reason. If an iterator ever becomes ownable, this is the
#              exemption to revisit.
EXEMPT_REASONS: dict[str, str] = {
    "ptr": "no methods (CE5011); not a generic type argument (CE5012); owns nothing",
    "blank": "not a value a user binds",
    "io handles": "not a value a user binds",
    "Iterator<T>": "a transient view; ownership belongs to what it iterates",
}


# ---------------------------------------------------------------------------
# Clause 1 -- the escape must exist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,ty", REPRESENTATIVE_TYPES, ids=[n for n, _ in REPRESENTATIVE_TYPES])
def test_a_move_type_has_a_clone(name, ty):
    """If consuming a borrow of `T` is CE2411, `T.clone()` must exist.

    This is the ownership table's own invariant restated: `(BORROWED, MOVE) -> REJECT`, and
    the only sanctioned way out of REJECT is the explicit copy.
    """
    if type_class_of(ty) is not TypeClass.MOVE:
        pytest.skip(f"{name} is not a MOVE type, so it never reaches REJECT")
    assert builtin_method_exists(ty, "clone"), (
        f"{name} is a MOVE type, so consuming a borrow of it is CE2411 -- and CE2411's help "
        f"text tells the user to call .clone(), which does not exist on it. That is a "
        f"rejection with no escape. See MM.md findings A5 and F1: this exact hole has "
        f"already stopped two phases."
    )


# ---------------------------------------------------------------------------
# Clause 2 -- monomorphization
# ---------------------------------------------------------------------------

# A type parameter is substituted with a concrete type and the ONE body is compiled for each.
# So `.clone()` must exist on every type that can be a type argument, not only on the owning
# ones -- `fn first@(T)(T[] arr) T` needs `elem.clone()` for `T = string` and is instantiated
# at `T = i32` as well. That is MM.md finding F1, and it is why a primitive has a clone that
# does nothing.
@pytest.mark.parametrize("name,ty", REPRESENTATIVE_TYPES, ids=[n for n, _ in REPRESENTATIVE_TYPES])
def test_a_generic_type_argument_has_a_clone(name, ty):
    """One monomorphized body must satisfy every instantiation of its type parameter."""
    assert builtin_method_exists(ty, "clone"), (
        f"{name} can be a generic type argument, so a body written as `x.clone()` for an "
        f"owning instantiation fails to compile when it is instantiated at {name}. The "
        f"escape must survive monomorphization -- Rust makes `Copy: Clone` for this reason. "
        f"See MM.md finding F1."
    )


# ---------------------------------------------------------------------------
# The auto-derived pair, which needs the analyzer to have run
# ---------------------------------------------------------------------------

def test_user_struct_and_enum_carry_a_clone(analyze):
    """Pass 1.8 registers clone from SEMANTICS, so the registry answer is import-order safe."""
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
    assert builtin_method_exists(bag, "clone"), "Pass 1.8 must auto-derive a struct clone"
    assert builtin_method_exists(holder, "clone"), "Pass 1.8 must auto-derive an enum clone"


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
    assert type_class_of(bag) is TypeClass.MOVE
    assert builtin_method_exists(StructType(name="Bag", fields=()), "clone")


# ---------------------------------------------------------------------------
# The one known hole, asserted rather than skipped
# ---------------------------------------------------------------------------

def test_hashmap_clone_is_a_known_hole_owned_by_phase_10():
    """`HashMap<K, V>` has no clone, and today it does not need one. Phase 10 changes that.

    `HashMap<` is absent from `is_owning_type` (typesys.py), so a HashMap classifies PLAIN,
    never reaches REJECT, and clause 1 passes over it vacuously. It is also excluded from
    Pass 1.8's auto-derivation (cloning.py CONTAINER_PREFIXES) and absent from
    `is_builtin_hashmap_method`, so no clone exists.

    MM.md's Phase 10 carries "name HashMap explicitly in the merged predicate". That edit
    makes a HashMap MOVE -- and in the SAME commit a borrowed HashMap at a consuming use
    becomes CE2411 with no escape. So `HashMap.clone()` belongs to Phase 10, which is why it
    is not in this branch.

    **When Phase 10 lands, clause 1 goes red on HashMap.** Add the clone, then delete this
    test -- it will already be failing, because the assertion below is that the hole EXISTS.
    """
    hashmap = StructType(name="HashMap<i32, i32>", fields=())
    assert type_class_of(hashmap) is not TypeClass.MOVE, (
        "HashMap is now a MOVE type, so Phase 10 has landed. HashMap.clone() is now "
        "mandatory -- clause 1 above is the real assertion. Delete this test."
    )
    assert not builtin_method_exists(hashmap, "clone"), (
        "HashMap.clone() now exists, so this known-hole test is stale. Delete it."
    )


def test_the_gate_can_actually_see_types():
    """Guard against the whole file passing because every case skipped or the list emptied."""
    assert len(REPRESENTATIVE_TYPES) >= 12
    moving = [n for n, t in REPRESENTATIVE_TYPES if type_class_of(t) is TypeClass.MOVE]
    assert len(moving) >= 4, f"clause 1 must actually bind to something; it bound to {moving}"
