"""Built-in clone() for the primitive types."""

from typing import Any

import llvmlite.ir as ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.generics.primitives import PRIMITIVE_METHOD_RETURNS
from sushi_lang.semantics.generics.primitives import validate_primitive_method
from sushi_lang.sushi_stdlib.src.common import BuiltinMethod, register_builtin_method


def _emit_clone(codegen: Any, call: MethodCall, receiver_value: ir.Value,
                receiver_type: ir.Type, to_i1: bool) -> ir.Value:
    """clone() on a primitive: the value is its own deep copy."""
    if len(call.args) != 0:
        raise_internal_error("CE0078", got=len(call.args))
    return receiver_value


# Registered from the semantics table, so the two cannot drift -- the same property
# `tests/unit/test_primitive_methods.py` asserts for to_str/hash/to_bits.
for _prim_type, _return_type in PRIMITIVE_METHOD_RETURNS["clone"].items():
    register_builtin_method(
        _prim_type,
        BuiltinMethod(
            name="clone",
            parameter_types=[],
            return_type=_return_type,
            description=f"Return an independent copy of a {_prim_type} (a plain copy)",
            semantic_validator=validate_primitive_method,
            llvm_emitter=_emit_clone,
        ),
    )
