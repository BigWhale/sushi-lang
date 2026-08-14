"""Built-in clone() for the primitive types.

`.clone()` is total over types, so every primitive carries one. A primitive owns no heap,
so the deep copy IS the value: the emitter returns the receiver unchanged and costs
nothing at runtime.

The method must exist even though it does nothing, because one monomorphized body has to
satisfy every instantiation of its type parameter. `fn first@(T)(T[] arr) T` needs
`elem.clone()` to detach a borrowed element when `T = string`, and the same body is
compiled for `T = i32`. Rust makes `Copy: Clone` for exactly this reason.

`string` is NOT registered here. It carries its own clone in the string method table,
which Pass 2 consults before the primitive path -- see
`semantics/generics/primitives.py::_CLONE_PRIMITIVES`.
"""

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
