"""The width of a stored count: the size of a string, the length and the capacity of a `T[]`
or a `List@(T)`, and the index and the length of an array cursor (docs/adr/0001).

A count is stored as the signed i64 of an index. `as_stored_i64` sign-extends a narrower
count. `as_surface_i32` gives the i32 that `size()`, `len()` and `capacity()` answer in the
expand step: every write extends an i32, so the stored value is in the i32 range and the
truncation loses nothing.
"""
from __future__ import annotations

from llvmlite import ir

STORED_TYPE = ir.IntType(64)
SURFACE_TYPE = ir.IntType(32)


def as_stored_i64(builder: ir.IRBuilder, value: ir.Value) -> ir.Value:
    """`value` at the stored width. An i64 is given as it is; a constant stays a constant."""
    if value.type == STORED_TYPE:
        return value
    if isinstance(value, ir.Constant) and isinstance(value.type, ir.IntType):
        return ir.Constant(STORED_TYPE, value.constant)
    return builder.sext(value, STORED_TYPE)


def as_surface_i32(builder: ir.IRBuilder, stored: ir.Value, name: str = "") -> ir.Value:
    """The stored i64 count as the surface i32."""
    return builder.trunc(stored, SURFACE_TYPE, name=name)
