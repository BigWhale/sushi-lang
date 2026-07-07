"""
Built-in bit-reinterpret methods for float primitive types.

Implemented methods:
- f32.to_bits() -> u32: Reinterpret the IEEE-754 bit pattern of an f32 as a u32
- f64.to_bits() -> u64: Reinterpret the IEEE-754 bit pattern of an f64 as a u64

These are the value-method half of the float<->bits reinterpret pair; the static
constructors f32.from_bits(u32) / f64.from_bits(u64) are handled separately in the
call dispatcher (they take a type-name receiver rather than a value).

Unlike the `as` cast (which is value-preserving and emits sitofp/fptosi), these emit a
single LLVM `bitcast` between equal-width types, exposing the raw IEEE-754 encoding. This
is the primitive needed to decode/encode MessagePack float32/float64 tags.
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import BuiltinType, Type
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_builtin_method, BuiltinMethod


# Validation function for to_bits() method
def _validate_to_bits(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate to_bits() method call on float primitive types (takes no arguments)."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{target_type}.to_bits", expected=0, got=len(call.args))


def _emit_to_bits(prim_type: BuiltinType) -> Any:
    """Create a to_bits() emitter for the given float primitive type.

    Args:
        prim_type: F32 or F64.

    Returns:
        An emitter function that bitcasts the float value to its integer bit pattern.
    """
    int_width = INT32_BIT_WIDTH if prim_type == BuiltinType.F32 else INT64_BIT_WIDTH

    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
                receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """to_bits() emitter created by factory."""
        if len(call.args) != 0:
            raise_internal_error("CE0078", got=len(call.args))
        builder = require_builder(codegen)
        return builder.bitcast(receiver_value, ir.IntType(int_width), name="to_bits")

    return emitter


# Register to_bits() for f32 (-> u32) and f64 (-> u64)
_TO_BITS_RETURN = {
    BuiltinType.F32: BuiltinType.U32,
    BuiltinType.F64: BuiltinType.U64,
}

for _prim_type, _return_type in _TO_BITS_RETURN.items():
    register_builtin_method(
        _prim_type,
        BuiltinMethod(
            name="to_bits",
            parameter_types=[],
            return_type=_return_type,
            description=f"Reinterpret the IEEE-754 bit pattern of {_prim_type} as {_return_type}",
            semantic_validator=_validate_to_bits,
            llvm_emitter=_emit_to_bits(_prim_type),
        ),
    )
