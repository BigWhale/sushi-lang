"""The primitive-method table in semantics must match what the backend registers.

Pass 2 decides which primitive methods exist (semantics/generics/primitives.py)
without consulting the builtin-method registry, because the compiler pipeline
imports the backend lazily -- after semantic analysis. The backend separately
registers the same methods with their LLVM emitters attached, and dispatches
emission through the registry.

Two tables, one truth. This test is what keeps them honest: add a primitive
method to one side only and it goes red.
"""

import pytest

from sushi_lang.semantics.generics.primitives import PRIMITIVE_METHOD_TYPES
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
    """The backend must not register a primitive method Pass 2 would reject as unknown."""
    carriers = PRIMITIVE_METHOD_TYPES[method_name]
    for prim_type in ALL_PRIMITIVES:
        if prim_type in carriers:
            continue
        assert get_builtin_method(prim_type, method_name) is None, (
            f"the backend registers {prim_type}.{method_name}(), but semantics does not "
            f"list {prim_type} as a carrier -- Pass 2 would report it undefined"
        )


def test_to_bits_is_float_only():
    """to_bits() exposes an IEEE-754 encoding, so it must not exist on integers."""
    assert PRIMITIVE_METHOD_TYPES["to_bits"] == frozenset({BuiltinType.F32, BuiltinType.F64})
    assert get_builtin_method(BuiltinType.I32, "to_bits") is None
