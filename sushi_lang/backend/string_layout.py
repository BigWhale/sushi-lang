"""The string value `{i8* data, i64 size, i8 owned}`: its type, its size field and its
construction (ADR 0001, docs/design/string-representation.md).

The size field is an i64. The surface `size()` and `len()` still answer an i32, so
`string_size` truncates and `string_value` extends an i32 size. `string_size_i64` gives the
stored width for a byte count (`malloc`, `mem*`). No other code reads or writes field 1.
"""
from __future__ import annotations

from typing import Union

from llvmlite import ir

DATA_FIELD = 0
SIZE_FIELD = 1
OWNED_FIELD = 2

SIZE_TYPE = ir.IntType(64)
SURFACE_SIZE_TYPE = ir.IntType(32)
_OWNED_TYPE = ir.IntType(8)

# The ABI layout: data at 0, size at 8, owned at 16, padding to the alignment 8. A string
# in an enum payload is `[3 x i64]`.
SIZE_OFFSET = 8
OWNED_OFFSET = 16
STRING_BYTES = 24


def string_type() -> ir.LiteralStructType:
    """`{i8* data, i64 size, i8 owned}`; `owned` is 1 for a heap buffer that RAII frees, and
    0 for a literal or a borrow."""
    return ir.LiteralStructType([ir.PointerType(ir.IntType(8)), SIZE_TYPE, _OWNED_TYPE])


def string_size(builder: ir.IRBuilder, value: ir.Value, name: str = "") -> ir.Value:
    """The size of a string value as the surface i32."""
    stored = builder.extract_value(value, SIZE_FIELD, name=f"{name}_i64" if name else "")
    return _surface(builder, stored, name)


def string_size_i64(builder: ir.IRBuilder, value: ir.Value, name: str = "") -> ir.Value:
    """The size of a string value at its stored width, for a byte count."""
    return builder.extract_value(value, SIZE_FIELD, name=name)


def string_size_at(builder: ir.IRBuilder, address: ir.Value, name: str = "") -> ir.Value:
    """The size of the string at `address` as the surface i32."""
    i32 = ir.IntType(32)
    slot = builder.gep(address, [ir.Constant(i32, 0), ir.Constant(i32, SIZE_FIELD)])
    stored = builder.load(slot, name=f"{name}_i64" if name else "")
    return _surface(builder, stored, name)


def with_string_size(builder: ir.IRBuilder, value: ir.Value, size: ir.Value,
                     name: str = "") -> ir.Value:
    """`value` with the size `size`; an i32 size is zero-extended."""
    return builder.insert_value(value, _stored(builder, size), SIZE_FIELD, name=name)


def string_value(builder: ir.IRBuilder, data: ir.Value, size: ir.Value,
                 owned: Union[int, ir.Value], name: str = "") -> ir.Value:
    """A string value over `data` with `size` bytes. `owned` is 0 or 1, or an i8 value."""
    flag = ir.Constant(_OWNED_TYPE, 1 if owned else 0) if isinstance(owned, int) else owned
    value = builder.insert_value(ir.Constant(string_type(), ir.Undefined), data, DATA_FIELD)
    value = with_string_size(builder, value, size)
    return builder.insert_value(value, flag, OWNED_FIELD, name=name)


def string_constant(data: ir.Constant, size: int, owned: int = 0) -> ir.Constant:
    """A string constant over the constant pointer `data`. Every field is concrete: on
    ARM64 `size` and `owned` share one by-value argument register, so an undef field
    poisons the other."""
    return ir.Constant.literal_struct([
        data, ir.Constant(SIZE_TYPE, size), ir.Constant(_OWNED_TYPE, 1 if owned else 0)])


def _surface(builder: ir.IRBuilder, stored: ir.Value, name: str) -> ir.Value:
    """The stored size as the surface i32. Every write extends an i32 size, so the value is
    in the i32 range and the truncation loses nothing."""
    return builder.trunc(stored, SURFACE_SIZE_TYPE, name=name)


def _stored(builder: ir.IRBuilder, size: ir.Value) -> ir.Value:
    """`size` at the stored width."""
    if size.type.width == SIZE_TYPE.width:
        return size
    return builder.zext(size, SIZE_TYPE)
