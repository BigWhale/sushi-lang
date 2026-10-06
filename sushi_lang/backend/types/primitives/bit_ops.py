"""Built-in bit methods on the unsigned integers: reverse_bits, leading_zeros, trailing_zeros.

The rule is in docs/language-reference.md, section "Bit Methods". A count of an all-zero
value is the width, so the intrinsics are called with is_zero_poison = false.
"""

from typing import Any, Callable

import llvmlite.ir as ir

from sushi_lang.backend.constants import INT32_BIT_WIDTH
from sushi_lang.backend.utils import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.common import BuiltinMethod, register_builtin_method


_UNSIGNED = (BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64)

_ZERO_IS_DEFINED = ir.Constant(ir.IntType(1), 0)


def _count_to_i32(builder: ir.IRBuilder, count: ir.Value, name: str) -> ir.Value:
    """Bring a count in the receiver's width to the i32 of every count."""
    i32 = ir.IntType(INT32_BIT_WIDTH)
    width = count.type.width
    if width < INT32_BIT_WIDTH:
        return builder.zext(count, i32, name=name)
    if width > INT32_BIT_WIDTH:
        return builder.trunc(count, i32, name=name)
    return count


def _reverse(builder: ir.IRBuilder, value: ir.Value) -> ir.Value:
    return builder.bitreverse(value, name="reverse_bits")


def _leading(builder: ir.IRBuilder, value: ir.Value) -> ir.Value:
    count = builder.ctlz(value, _ZERO_IS_DEFINED, name="ctlz")
    return _count_to_i32(builder, count, "leading_zeros")


def _trailing(builder: ir.IRBuilder, value: ir.Value) -> ir.Value:
    count = builder.cttz(value, _ZERO_IS_DEFINED, name="cttz")
    return _count_to_i32(builder, count, "trailing_zeros")


_EMITTERS: dict[str, Callable[[ir.IRBuilder, ir.Value], ir.Value]] = {
    "reverse_bits": _reverse,
    "leading_zeros": _leading,
    "trailing_zeros": _trailing,
}


def _emit_bit_method(codegen: Any, call: MethodCall, receiver_value: ir.Value,
                     receiver_type: ir.Type, to_i1: bool) -> ir.Value:
    if len(call.args) != 0:
        raise_internal_error("CE0023", method=call.method, expected=0, got=len(call.args))
    return _EMITTERS[call.method](require_builder(codegen), receiver_value)


for _prim_type in _UNSIGNED:
    for _name, _returns, _what in (
        ("reverse_bits", _prim_type, "the bits in the reverse order"),
        ("leading_zeros", BuiltinType.I32, "the count of zero bits above the highest one bit"),
        ("trailing_zeros", BuiltinType.I32, "the count of zero bits below the lowest one bit"),
    ):
        register_builtin_method(
            _prim_type,
            BuiltinMethod(
                name=_name,
                return_type=_returns,
                description=f"{_prim_type}: {_what}",
                llvm_emitter=_emit_bit_method,
            ),
        )
