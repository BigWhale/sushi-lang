"""An enum constant as its `{i32 tag, [K x i64] data}` initializer (#551).

A payload-free variant is a tag over the zero value. A payload-carrying one is the tag
plus its constant payloads, written into the data words at the offsets
`TypeSizing.payload_field_offsets` gives -- the ONE layout authority, so a constant and
a run-time construction cannot disagree about where a field sits. The bytes are
little-endian, which is every platform Sushi ships on (x86_64, arm64).

A pointer -- the data word of a string's fat pointer -- cannot be spelled as bytes: it
is a relocation. It is 8-aligned and 8 bytes wide wherever it sits, so it always fills
exactly one word, and that word becomes a `ptrtoint` constant expression instead.
"""
from __future__ import annotations

import struct
from typing import Callable, Dict, List, Optional, Sequence, TYPE_CHECKING

from llvmlite import ir

from sushi_lang.semantics.typesys import (
    BuiltinType, StructType, EnumType, ArrayType, Type)
from sushi_lang.backend.constants.sizes import ENUM_TAG_SIZE_BYTES

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.const_eval import ConstantValue

# `(text, data_name)` -> the `{i8* data, i32 size, i8 owned}` constant, with its backing
# bytes placed in the module. None where no module is at hand, and a string payload then
# answers None exactly as a bare string constant does.
StringFinisher = Callable[[str, str], ir.Constant]

WORD_BYTES = 8

# The string fat pointer's layout, `{i8* data, i32 size, i8 owned}`
# (docs/design/string-representation.md): the data word at 0, the size at 8, owned at 12.
STRING_SIZE_OFFSET = 8
STRING_OWNED_OFFSET = 12

_INTEGER_TYPES = (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                  BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64)


class _PayloadBytes:
    """The payload's bytes, plus the words a pointer fills instead."""

    def __init__(self, size: int):
        self.bytes = bytearray(size)
        self.pointers: Dict[int, ir.Constant] = {}

    def words(self, i64: ir.Type) -> List[ir.Constant]:
        """The data words: a pointer's word is its `ptrtoint`, every other is its bytes."""
        words = []
        for index in range(len(self.bytes) // WORD_BYTES):
            pointer = self.pointers.get(index)
            if pointer is not None:
                words.append(pointer.ptrtoint(i64))
                continue
            chunk = self.bytes[index * WORD_BYTES:(index + 1) * WORD_BYTES]
            words.append(ir.Constant(i64, int.from_bytes(chunk, "little", signed=True)))
        return words


def enum_initializer(value: 'ConstantValue', types, finish_string: Optional[StringFinisher],
                     data_name: str) -> Optional[ir.Constant]:
    """The `{i32 tag, [K x i64] data}` constant for an evaluated enum value.

    None when a payload needs a module and none was given (a string, with no finisher),
    which is the same answer `const_value_to_llvm` gives a bare string.
    """
    enum_type = value.semantic_type
    llvm_enum = types.get_enum_type(enum_type)
    data_type = llvm_enum.elements[1]

    tag = enum_type.get_variant_index(value.variant)
    if tag is None:
        return None
    if not value.value:
        data = ir.Constant(data_type, None)
    else:
        blob = _PayloadBytes(data_type.count * WORD_BYTES)
        variant = enum_type.get_variant(value.variant)
        if not _write_fields(blob, 0, variant.associated_types, value.value, types,
                             finish_string, data_name):
            return None
        data = ir.Constant(data_type, blob.words(types.i64))
    return ir.Constant(llvm_enum, [ir.Constant(types.i32, tag), data])


def _write_fields(blob: _PayloadBytes, base: int, field_types: Sequence[Type],
                  values: Sequence['ConstantValue'], types,
                  finish_string: Optional[StringFinisher], data_name: str) -> bool:
    """Write a naturally aligned field sequence -- a payload, or a struct's fields."""
    offsets = types.payload_field_offsets(list(field_types))
    for index, (field_value, offset) in enumerate(zip(values, offsets, strict=True)):
        if not _write(blob, base + offset, field_value, types, finish_string,
                      f"{data_name}.{index}"):
            return False
    return True


def _write(blob: _PayloadBytes, offset: int, value: 'ConstantValue', types,
           finish_string: Optional[StringFinisher], data_name: str) -> bool:
    """Write one constant value at `offset`; False when it needs a module and has none."""
    ty = value.semantic_type

    if ty == BuiltinType.BOOL:
        blob.bytes[offset] = 1 if value.value else 0
        return True

    if ty in _INTEGER_TYPES:
        size = types.get_type_size_bytes(ty)
        blob.bytes[offset:offset + size] = (value.value % (1 << (8 * size))).to_bytes(
            size, "little")
        return True

    if ty == BuiltinType.F32:
        blob.bytes[offset:offset + 4] = struct.pack("<f", value.value)
        return True

    if ty == BuiltinType.F64:
        blob.bytes[offset:offset + 8] = struct.pack("<d", value.value)
        return True

    if ty == BuiltinType.STRING:
        if finish_string is None:
            return False
        data_pointer, size, owned = finish_string(value.value, data_name).constant
        blob.pointers[offset // WORD_BYTES] = data_pointer
        size_at = offset + STRING_SIZE_OFFSET
        blob.bytes[size_at:size_at + 4] = size.constant.to_bytes(4, "little")
        blob.bytes[offset + STRING_OWNED_OFFSET] = owned.constant
        return True

    if isinstance(ty, StructType):
        return _write_fields(blob, offset, [field_type for _name, field_type in ty.fields],
                             value.value, types, finish_string, data_name)

    if isinstance(ty, EnumType):
        tag = ty.get_variant_index(value.variant)
        if tag is None:
            return False
        blob.bytes[offset:offset + 4] = tag.to_bytes(4, "little")
        if not value.value:
            return True
        variant = ty.get_variant(value.variant)
        return _write_fields(blob, offset + ENUM_TAG_SIZE_BYTES, variant.associated_types,
                             value.value, types, finish_string, data_name)

    if isinstance(ty, ArrayType):
        stride = types.get_type_size_bytes(ty.base_type)
        for index, element in enumerate(value.value):
            if not _write(blob, offset + index * stride, element, types, finish_string,
                          f"{data_name}.{index}"):
                return False
        return True

    return False
