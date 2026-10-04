"""Every callee kind agrees with its declared signature."""
from __future__ import annotations

import pytest

from sushi_lang.backend.functions.helpers import callee_owns_param
from sushi_lang.semantics.param_modes import (
    CalleeKind,
    ParamMode,
    receiver_mode,
)


# The kinds whose parameters are DECLARED in Sushi source. The other three consume by
# position and declare nothing: a struct field, an enum payload and a container slot.
DECLARING_KINDS = [
    CalleeKind.FUNCTION,
    CalleeKind.METHOD,
    CalleeKind.STATIC_METHOD,
    CalleeKind.STDLIB,
    CalleeKind.FFI_EXTERN,
    CalleeKind.INDIRECT,
]

POSITIONAL_KINDS = [
    CalleeKind.CONSTRUCTOR,
    CalleeKind.CONTAINER,
]


def test_every_callee_kind_is_in_exactly_one_group():
    assert set(DECLARING_KINDS) | set(POSITIONAL_KINDS) == set(CalleeKind)
    assert not set(DECLARING_KINDS) & set(POSITIONAL_KINDS)


# 3. The callee registers cleanup iff the mode is nom


def test_ownership_does_not_depend_on_the_kind_of_callee():
    """The whole point: the same declaration means the same thing everywhere."""
    import inspect
    signature = inspect.signature(callee_owns_param)
    assert list(signature.parameters) == ["param"]


# 5. The RECEIVER's mode is read through the same seam

RECEIVER_DECLARATIONS = {
    ParamMode.BORROW: None,
    ParamMode.PEEK: "peek",
    ParamMode.POKE: "poke",
    ParamMode.NOM: "nom",
}


@pytest.mark.parametrize("mode,marker", list(RECEIVER_DECLARATIONS.items()))
def test_the_receiver_mode_resolver_is_total(mode, marker):
    """Every `ParamMode` has a receiver spelling, and the resolver answers each one."""
    assert receiver_mode(marker) is mode


def test_an_unmarked_receiver_borrows_like_an_unmarked_parameter():
    assert receiver_mode(None) is ParamMode.BORROW
    assert not receiver_mode(None).consumes
    assert not receiver_mode(None).by_pointer


def test_only_the_consuming_receiver_takes_ownership():
    consuming = [m for m in RECEIVER_DECLARATIONS
                 if receiver_mode(RECEIVER_DECLARATIONS[m]).consumes]
    assert consuming == [ParamMode.NOM]


# 6. A BUILT-IN method reads its parameter modes from its family's table (#1173)


def _family_methods(family) -> frozenset:
    if family.arity:
        return frozenset(family.arity)
    if family.name == "array":
        from sushi_lang.semantics.passes.types.arrays import _ARRAY_METHODS
        return frozenset(_ARRAY_METHODS)
    return frozenset()


def test_every_row_and_slot_of_a_family_table_names_a_method_of_the_family():
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
    for family in METHOD_TYPE_REGISTRY.families:
        named = set(family.modes.rows) | set(family.modes.slots)
        if named:
            assert named <= _family_methods(family), family.name


def test_a_container_slot_is_a_method_that_stores_its_arguments():
    """The slots of the tables are the borrow pass's container inserts, and `Own.alloc`."""
    from sushi_lang.semantics.method_effects import CONTAINER_INSERT_METHODS
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
    slots = set().union(*(f.modes.slots for f in METHOD_TYPE_REGISTRY.families))
    assert slots == set(CONTAINER_INSERT_METHODS) | {"alloc"}


def test_a_signature_row_and_its_family_table_agree():
    from sushi_lang.semantics.generics.maybe import MAYBE_METHOD_MODES, MAYBE_METHOD_SIGNATURES
    from sushi_lang.semantics.generics.results import (
        RESULT_METHOD_MODES, RESULT_METHOD_SIGNATURES)
    from sushi_lang.semantics.param_modes import declared_modes
    for table, rows in ((MAYBE_METHOD_MODES, MAYBE_METHOD_SIGNATURES),
                        (RESULT_METHOD_MODES, RESULT_METHOD_SIGNATURES)):
        for name, signature in rows.items():
            assert table.of(name, len(signature.params)) == declared_modes(signature.params)


def test_a_method_with_no_row_borrows_each_argument():
    from sushi_lang.semantics.param_modes import BuiltinModes
    assert BuiltinModes().of("get", 2) == (ParamMode.BORROW, ParamMode.BORROW)
    assert BuiltinModes(slots=frozenset({"push"})).of("push", 1) is None
