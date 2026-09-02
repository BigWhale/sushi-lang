"""The MOVE class, the recursion gate and the registration gate answer as one.

There used to be three implementations of "does this own something RAII must release":
`typesys.owns_heap` decided the MOVE class, `destructors.needs_cleanup` gated destructor
RECURSION, and a field walk in `memory/dynamic_arrays.py` gated cleanup REGISTRATION. The
comment that walk carried named the hazard outright -- "the two disagreeing is exactly
what #162/#183 were".

A `Drop` type is where they parted company, and it is why HANDLES.md ruling R2a collapsed
them. A handle holds one `i32` descriptor, so a field walk finds nothing: the MOVE class
said "this moves" while the registration gate said "nothing to clean", and the value moved
correctly and never had its `drop()` called. There is one predicate now, and this gate is
what keeps it one.
"""
from __future__ import annotations

import pytest

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.destructors import needs_cleanup
from sushi_lang.semantics.ownership import TypeClass, type_class_of
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    EnumVariantInfo,
    StructType,
    owns_resource,
)


HANDLE = StructType(name="Handle", fields=(("fd", BuiltinType.I32),))
WRAPPER = StructType(name="Wrapper", fields=(("inner", HANDLE), ("tag", BuiltinType.I32)))
PLAIN = StructType(name="Plain", fields=(("x", BuiltinType.I32),))
OWNING = StructType(name="Owning", fields=(("s", BuiltinType.STRING),))
HOLDER = EnumType(name="Holder", variants=(
    EnumVariantInfo(name="Full", associated_types=(HANDLE,)),
    EnumVariantInfo(name="Empty", associated_types=()),
))

# `Handle` is the only DECLARED resource. Everything else answers by structure.
DROPS = frozenset({"Handle"})


def _codegen() -> LLVMCodegen:
    """A codegen whose tables know these types and which of them implement `Drop`."""
    codegen = LLVMCodegen("predicate_agreement")
    for ty in (HANDLE, WRAPPER, PLAIN, OWNING):
        codegen.struct_table.by_name[ty.name] = ty
    codegen.enum_table.by_name[HOLDER.name] = HOLDER
    codegen.perk_impl_table.by_perk["Drop"] = set(DROPS)
    return codegen


# name -> (type, what every predicate must answer)
CASES: list[tuple[str, object, bool]] = [
    ("Handle (declares Drop)", HANDLE, True),
    ("Wrapper (holds a Drop type)", WRAPPER, True),
    ("Holder (a Drop payload)", HOLDER, True),
    ("Handle[] (dynamic array)", DynamicArrayType(base_type=HANDLE), True),
    ("Handle[3] (fixed array)", ArrayType(base_type=HANDLE, size=3), True),
    ("Owning (a string field)", OWNING, True),
    ("Plain (one i32)", PLAIN, False),
    ("i32", BuiltinType.I32, False),
    ("bool", BuiltinType.BOOL, False),
]


@pytest.mark.parametrize("name,ty,expected", CASES, ids=[c[0] for c in CASES])
def test_every_cleanup_predicate_gives_one_answer(name, ty, expected):
    """One rule, one answer, whichever layer asks it."""
    codegen = _codegen()

    move = type_class_of(ty, DROPS) is TypeClass.MOVE
    structural = owns_resource(ty, DROPS)
    backend = needs_cleanup(codegen, ty)

    assert move is expected, f"{name}: the MOVE class disagrees"
    assert structural is expected, f"{name}: owns_resource disagrees"
    assert backend is expected, (
        f"{name}: the backend cleanup predicate disagrees with the MOVE class. A value "
        f"that MOVES but registers no cleanup never has its destructor run, which for a "
        f"resource type is a leaked descriptor with no diagnostic."
    )


def test_the_drop_set_is_what_makes_the_difference():
    """Without the answer, a handle reads as owning nothing -- the ruling R2a hazard.

    A caller that cannot supply the `Drop` set must not compile, and this is why: the
    SAME type answers PLAIN when the set is empty. A forgotten argument would be a
    leaked descriptor, silently.
    """
    assert owns_resource(HANDLE, frozenset()) is False
    assert owns_resource(HANDLE, DROPS) is True
    assert owns_resource(WRAPPER, frozenset()) is False
    assert owns_resource(WRAPPER, DROPS) is True

    with pytest.raises(TypeError):
        owns_resource(HANDLE)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        type_class_of(HANDLE)  # type: ignore[call-arg]


def test_a_string_owns_resource_but_declares_none():
    """`owns_resource` and `holds_declared_resource` are different questions.

    The narrower one is what `.clone()` is refused on: a string deep-copies perfectly
    well, and refusing to clone one would break every program in the suite.
    """
    from sushi_lang.semantics.typesys import holds_declared_resource

    assert owns_resource(OWNING, DROPS) is True
    assert holds_declared_resource(OWNING, DROPS) is False

    assert holds_declared_resource(HANDLE, DROPS) is True
    assert holds_declared_resource(WRAPPER, DROPS) is True
    assert holds_declared_resource(DynamicArrayType(base_type=HANDLE), DROPS) is True
    assert holds_declared_resource(PLAIN, DROPS) is False
