"""Type inference helpers for type validation."""
from __future__ import annotations
from typing import Optional, Protocol

from sushi_lang.semantics.integer_width import fits_integer_type, integer_bit_width
from sushi_lang.semantics.typesys import BuiltinType


_F32_MAX = 3.4028234663852886e38


class IntegerLiteral(Protocol):
    """An integer literal of the AST or of SHIR: its byte spelling, or None."""
    @property
    def byte_spelling(self) -> Optional[str]: ...


def int_literal_default(lit: IntegerLiteral) -> BuiltinType:
    """The type of an integer literal that no position has typed: u8 for a byte literal
    (`a'x'`), i32 for a number literal. The one answer for every pass and the backend, for
    an AST literal and a SHIR literal alike."""
    return BuiltinType.U8 if lit.byte_spelling is not None else BuiltinType.I32


def int_literal_fits(value: int, radix: int, target_type: BuiltinType) -> bool:
    """Check whether an integer literal fits its context-typed target.

    A literal written in another base spells BITS, so the whole unsigned range of the
    width is legal: 0xFF is an i8, and -1 is the value it stands for.
    """
    width = integer_bit_width(target_type)
    if width is None:
        return False
    if radix == 10:
        return fits_integer_type(value, target_type)
    return 0 <= value <= (1 << width) - 1


def float_literal_fits(value: float, target_type: BuiltinType) -> bool:
    """Check whether a float literal fits its context-typed target."""
    if target_type == BuiltinType.F64:
        return True
    if target_type == BuiltinType.F32:
        return abs(value) <= _F32_MAX
    return False
