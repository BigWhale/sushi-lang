"""The width of an integer type, and what that width does to a value.

Two language rules ask this question and must give one answer: a literal fits its type
or it does not (CE2070, CE2073), and an operation the compiler reads gives a value the
type can hold or it does not (CE2077). One table answers both, so a width cannot be
right in one rule and wrong in the other.

`wrap_to_integer_type` is the two's-complement answer a width-defined operator gives:
the bits that leave the type are lost, which is what the machine does at run time.
`docs/design/compile-time-evaluation.md` is normative for which operator gets which.
"""
from __future__ import annotations

from typing import Mapping, Optional, Tuple

from sushi_lang.semantics.type_predicates import is_unsigned_int
from sushi_lang.semantics.typesys import BuiltinType, Type

INTEGER_BIT_WIDTHS: Mapping[BuiltinType, int] = {
    BuiltinType.I8: 8, BuiltinType.U8: 8,
    BuiltinType.I16: 16, BuiltinType.U16: 16,
    BuiltinType.I32: 32, BuiltinType.U32: 32,
    BuiltinType.I64: 64, BuiltinType.U64: 64,
}


def integer_bit_width(ty: Optional[Type]) -> Optional[int]:
    """The bit width of an integer type, None when the type is not an integer."""
    return INTEGER_BIT_WIDTHS.get(ty)


def integer_range(ty: Optional[Type]) -> Optional[Tuple[int, int]]:
    """The inclusive range an integer type can hold, None when it is not an integer."""
    width = INTEGER_BIT_WIDTHS.get(ty)
    if width is None:
        return None
    if is_unsigned_int(ty):
        return 0, (1 << width) - 1
    half = 1 << (width - 1)
    return -half, half - 1


def fits_integer_type(value: int, ty: Optional[Type]) -> bool:
    """Whether an integer type can hold a value."""
    bounds = integer_range(ty)
    if bounds is None:
        return False
    return bounds[0] <= value <= bounds[1]


def wrap_to_integer_type(value: int, ty: Optional[Type]) -> int:
    """The low bits of a value, with the sign its type gives them.

    The value is left alone for a type that has no width, because the caller has
    nothing better to answer with; every integer type has one.
    """
    width = INTEGER_BIT_WIDTHS.get(ty)
    if width is None:
        return value
    wrapped = value & ((1 << width) - 1)
    if not is_unsigned_int(ty) and wrapped >= (1 << (width - 1)):
        wrapped -= 1 << width
    return wrapped
