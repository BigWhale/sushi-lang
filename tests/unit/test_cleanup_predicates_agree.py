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

from sushi_lang.semantics.ownership import type_class_of
from sushi_lang.semantics.typesys import (
    BuiltinType,
    DynamicArrayType,
    StructType,
    owns_resource,
)


HANDLE = StructType(name="Handle", fields=(("fd", BuiltinType.I32),))
WRAPPER = StructType(name="Wrapper", fields=(("inner", HANDLE), ("tag", BuiltinType.I32)))
PLAIN = StructType(name="Plain", fields=(("x", BuiltinType.I32),))
OWNING = StructType(name="Owning", fields=(("s", BuiltinType.STRING),))

# `Handle` is the only DECLARED resource. Everything else answers by structure.
DROPS = frozenset({"Handle"})




# name -> (type, what every predicate must answer)




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
