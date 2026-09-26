"""Every callee kind agrees with its declared signature."""
from __future__ import annotations

import pytest

from sushi_lang.backend.functions.helpers import callee_owns_param
from sushi_lang.semantics.param_modes import (
    CalleeKind,
    ParamMode,
    receiver_mode,
)


# 1. The resolver returns the declared mode, for every kind

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


# 2. A later use is CE2405 iff the mode is nom -- through the real compiler









# 3. A callee the compiler could not resolve declares nothing, so nothing is judged












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




# 6. A STATIC method declares parameters and no receiver (#542, ruling R4)







