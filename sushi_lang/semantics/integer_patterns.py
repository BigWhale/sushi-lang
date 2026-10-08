"""The values an integer pattern matches: one inclusive interval of its type.

A literal arm and a range arm read a value of an integer type. A non-decimal literal is a
bit pattern of that type, so `0xff` on an i8 is -1, the value that `-1` also spells. The
duplicate rule, the overlap rule, the cover rule and the backend must all read one value,
so they all read it here.

The answer depends on the TYPE at the position, which the caller gives, not on the pattern
alone. The pattern holds no stamp: the typecheck pass and the backend both read the value
here, so they cannot disagree. A type parameter is opaque in a template, so an integer
pattern on a `T` is refused (CE2119) before any instance is made.
"""
from __future__ import annotations

from typing import Optional, Tuple, Union

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.ast import LiteralPattern, RangePattern
from sushi_lang.semantics.integer_width import wrap_to_integer_type
from sushi_lang.semantics.typesys import Type

Interval = Tuple[int, int]


def literal_value(pattern: LiteralPattern, ty: Optional[Type]) -> int:
    """The value an integer literal pattern matches at a position of type `ty`."""
    if not isinstance(pattern.value, int):
        raise_internal_error("CE0015", message=f"'{pattern.display}' is read as an integer")
        return 0
    return wrap_to_integer_type(pattern.value, ty)


def range_bounds(pattern: RangePattern, ty: Optional[Type]) -> Interval:
    """The two written bounds of a range, as values of `ty`: (start, end)."""
    return literal_value(pattern.low, ty), literal_value(pattern.high, ty)


def goes_down(pattern: RangePattern, ty: Optional[Type]) -> bool:
    """A range whose start is above its end (CE2125). `5..5` is empty and does not go down."""
    start, end = range_bounds(pattern, ty)
    return start > end


def pattern_interval(pattern: Union[LiteralPattern, RangePattern],
                     ty: Optional[Type]) -> Interval:
    """The inclusive interval (low, high) of values a pattern matches. low > high: none."""
    if isinstance(pattern, LiteralPattern):
        value = literal_value(pattern, ty)
        return value, value
    start, end = range_bounds(pattern, ty)
    return start, end if pattern.inclusive else end - 1
