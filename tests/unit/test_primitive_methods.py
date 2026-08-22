"""The primitive-method table in semantics must match what the backend registers."""

import pytest

from sushi_lang.semantics.generics.primitives import (
    PRIMITIVE_METHOD_RETURNS,
    PRIMITIVE_METHOD_TYPES,
    primitive_method_return_type,
)
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.common import get_builtin_method

# Importing the backend package runs the registrations under test.
import sushi_lang.backend.types.primitives  # noqa: F401


ALL_PRIMITIVES = [
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING,
]


@pytest.mark.parametrize("method_name", sorted(PRIMITIVE_METHOD_TYPES))
def test_semantics_table_is_registered_by_the_backend(method_name):
    """Every (type, method) semantics claims exists must be registered with an emitter."""
    for prim_type in PRIMITIVE_METHOD_TYPES[method_name]:
        method = get_builtin_method(prim_type, method_name)
        assert method is not None, (
            f"semantics claims {prim_type}.{method_name}() exists, "
            f"but the backend registers no such method"
        )
        assert method.llvm_emitter is not None, (
            f"{prim_type}.{method_name}() is registered without an emitter"
        )


@pytest.mark.parametrize("method_name", sorted(PRIMITIVE_METHOD_TYPES))
def test_backend_registers_nothing_semantics_does_not_know_about(method_name):
    """The backend must not register a primitive method the typecheck pass would reject as unknown."""
    carriers = PRIMITIVE_METHOD_TYPES[method_name]
    for prim_type in ALL_PRIMITIVES:
        if prim_type in carriers:
            continue
        assert get_builtin_method(prim_type, method_name) is None, (
            f"the backend registers {prim_type}.{method_name}(), but semantics does not "
            f"list {prim_type} as a carrier -- the typecheck pass would report it undefined"
        )


def test_to_bits_is_float_only():
    """to_bits() exposes an IEEE-754 encoding, so it must not exist on integers."""
    assert PRIMITIVE_METHOD_TYPES["to_bits"] == frozenset({BuiltinType.F32, BuiltinType.F64})
    assert get_builtin_method(BuiltinType.I32, "to_bits") is None


# Return types (#239)
#
# The tests above assert method NAMES only, and they warm the registry themselves with the
# backend import at the top of this file. Both properties held perfectly while primitive
# return-type inference was dead for eight weeks: the typecheck pass reads the semantics-side table, and
# in the real pipeline the registry is still empty when it does. These pin the axis that
# actually broke, on the table the typecheck pass actually reads.


def test_returns_table_is_the_authority_for_carriers():
    """PRIMITIVE_METHOD_TYPES is a derived view, so the two cannot disagree."""
    assert set(PRIMITIVE_METHOD_RETURNS) == set(PRIMITIVE_METHOD_TYPES)
    for name, returns in PRIMITIVE_METHOD_RETURNS.items():
        assert frozenset(returns) == PRIMITIVE_METHOD_TYPES[name]


@pytest.mark.parametrize("prim_type", ALL_PRIMITIVES)
def test_to_str_returns_string_everywhere(prim_type):
    assert primitive_method_return_type(prim_type, "to_str") is BuiltinType.STRING


@pytest.mark.parametrize("prim_type", ALL_PRIMITIVES)
def test_hash_returns_u64_everywhere(prim_type):
    assert primitive_method_return_type(prim_type, "hash") is BuiltinType.U64


def test_to_bits_width_follows_the_receiver():
    """The receiver-dependent case, and the reason the table is keyed per (method, type)."""
    assert primitive_method_return_type(BuiltinType.F32, "to_bits") is BuiltinType.U32
    assert primitive_method_return_type(BuiltinType.F64, "to_bits") is BuiltinType.U64
    assert primitive_method_return_type(BuiltinType.I32, "to_bits") is None


def test_string_carries_the_primitive_methods():
    """string is a primitive here too -- excluding it left string.to_str()/hash() dead."""
    assert primitive_method_return_type(BuiltinType.STRING, "to_str") is BuiltinType.STRING
    assert primitive_method_return_type(BuiltinType.STRING, "hash") is BuiltinType.U64


def test_unknown_pairs_return_none():
    assert primitive_method_return_type(BuiltinType.I32, "nope") is None
    assert primitive_method_return_type(BuiltinType.BOOL, "to_bits") is None


@pytest.mark.parametrize("method_name", sorted(PRIMITIVE_METHOD_TYPES))
def test_semantics_return_type_matches_the_backend_registration(method_name):
    """The semantics table and the backend's BuiltinMethod must agree on the return type."""
    for prim_type in PRIMITIVE_METHOD_TYPES[method_name]:
        registered = get_builtin_method(prim_type, method_name)
        assert registered is not None, f"{prim_type}.{method_name}() is not registered"
        assert registered.return_type == primitive_method_return_type(prim_type, method_name), (
            f"{prim_type}.{method_name}(): semantics says "
            f"{primitive_method_return_type(prim_type, method_name)}, "
            f"the backend registers {registered.return_type}"
        )
