"""An aggregate constant as its LLVM initializer: a struct, a tuple, an array and an enum variant (#551).

The one converter of an aggregate constant: the SLIR emitter and the declaration round of a
global both call `constant_initializer` with a SLIR `Constant` (the round makes one from
the evaluator value through `constant_of`, over `constants.of_value`). The converter first
runs the one check of a variant (`checked_variant`), of a struct (`checked_struct`) and of
an array over the value, at every depth; the rvalues of the SLIR emitter call the same
checks. So a value that it cannot write after the check is an internal error. A struct is
one LLVM constant for each field, in the declaration order, and an array one for each
element. An enum is its `{i32 tag, [K x i64] data}`
initializer (`enum_initializer`). A payload-free variant is a tag over the zero value. A payload-carrying one is the tag plus its constant payloads, written into the
data words at the offsets `TypeSizing.payload_field_offsets` gives -- the ONE layout
authority, so a constant and a run-time construction cannot disagree about where a field
sits. Each value is written at the declared type of its field. The bytes are
little-endian, which is every platform Sushi ships on (x86_64, arm64).

A pointer -- the data word of a string's fat pointer -- cannot be spelled as bytes: it
is a relocation. It is 8-aligned and 8 bytes wide wherever it sits, so it always fills
exactly one word, and that word becomes a `ptrtoint` constant expression instead.
"""
from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Callable, Dict, List, NoReturn, Optional, Sequence

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Span
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.typesys import (
    FunctionType,
    BuiltinType, StructType, EnumType, EnumVariantInfo, ArrayType, Type, deref_type)
from sushi_lang.backend.constants.sizes import ENUM_TAG_SIZE_BYTES
from sushi_lang.backend.string_layout import OWNED_OFFSET, SIZE_OFFSET, SIZE_TYPE

if TYPE_CHECKING:
    from sushi_lang.semantics.const_eval import AggregateConstant

# `(text, data_name)` -> the string constant, with its backing
# bytes placed in the module.
StringFinisher = Callable[[str, str], ir.Constant]

# A function symbol -> the `{thunk, null, null, null}` constant of its value.
FunctionFinisher = Callable[[str], ir.Constant]

# The internal error of a caller, with the detail of the fault.
Fault = Callable[[str], NoReturn]

WORD_BYTES = 8


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


def constant_of(value: 'AggregateConstant', span: Optional[Span],
                filename: Optional[str]) -> k.Aggregate:
    """The SLIR constant of a struct, an enum or an array value of the evaluator."""
    constant = k.of_value(value)
    if not isinstance(constant, k.Aggregate):
        raise_internal_error("CE0000", span=span, filename=filename,
                             detail=f"the aggregate value {value} has a scalar constant")
    return constant


def enum_of(ty: Type, fault: Fault) -> EnumType:
    """`ty`, which must be an enum."""
    if not isinstance(ty, EnumType):
        fault(f"{ty} is no enum")
    return ty


def variant_of(ty: Type, variant: str, fault: Fault) -> tuple[EnumType, EnumVariantInfo]:
    """The enum `ty` and its variant `variant`."""
    enum_type = enum_of(ty, fault)
    info = enum_type.get_variant(variant)
    if info is None:
        fault(f"{enum_type} has no variant '{variant}'")
    return enum_type, info


def checked_variant(ty: Type, variant: str, parts: Sequence[Type],
                    fault: Fault) -> tuple[EnumType, EnumVariantInfo]:
    """The one check of an `Aggregate` of a variant, as an rvalue and as a constant:
    `variant` is a variant of the enum `ty`, and the semantic type of each part is the type
    of its payload field."""
    enum_type, info = variant_of(ty, variant, fault)
    if tuple(parts) != tuple(deref_type(payload) for payload in info.associated_types):
        fault(f"the payload of {enum_type}.{variant} is "
              f"({', '.join(map(str, parts))}), not "
              f"({', '.join(map(str, info.associated_types))})")
    return enum_type, info


def checked_struct(ty: Type, parts: Sequence[Type], fault: Fault) -> StructType:
    """The one check of an `Aggregate` of a struct or a tuple, as an rvalue and as a
    constant: `ty` is a struct, and the semantic type of each part is the type of its
    field."""
    if not isinstance(ty, StructType):
        fault(f"{ty} is no struct")
    fields = tuple(deref_type(field_type) for _name, field_type in ty.fields)
    if tuple(parts) != fields:
        fault(f"the fields of {ty} are ({', '.join(map(str, fields))}), not "
              f"({', '.join(map(str, parts))})")
    return ty


def _check_constant(constant: k.Constant, fault: Fault) -> None:
    """The one check for each variant and each struct in a constant, at every depth."""
    if isinstance(constant, k.Aggregate) and not isinstance(constant.ty, FunctionType):
        parts = [semantic_type_of(part, fault) for part in constant.parts]
        if constant.variant is not None:
            checked_variant(constant.ty, constant.variant, parts, fault)
        elif isinstance(constant.ty, ArrayType):
            if parts != [constant.ty.base_type] * constant.ty.size:
                fault(f"the elements of {constant.ty} are ({', '.join(map(str, parts))})")
        else:
            checked_struct(constant.ty, parts, fault)
        for part in constant.parts:
            _check_constant(part, fault)


def semantic_type_of(constant: k.Constant, fault: Fault) -> Type:
    """The semantic type of a constant."""
    if isinstance(constant, (k.Int, k.Float, k.Aggregate)):
        return constant.ty
    if isinstance(constant, k.Bool):
        return BuiltinType.BOOL
    if isinstance(constant, k.Blank):
        return BuiltinType.BLANK
    if isinstance(constant, k.Str):
        return BuiltinType.STRING
    fault(f"the constant {constant} has no type of a payload")


def constant_initializer(value: k.Aggregate, types, finish_string: Optional[StringFinisher],
                         data_name: str, *, span: Optional[Span] = None,
                         filename: Optional[str] = None,
                         finish_function: Optional[FunctionFinisher] = None
                         ) -> ir.Constant:
    """The LLVM constant of an aggregate constant: an enum variant through
    `enum_initializer`, a struct or a tuple as one constant for each field, an array as one
    constant for each element. A string part is the constant of `finish_string`, named
    `<data_name>.<index>`. A function value is the constant of `finish_function`."""
    if isinstance(value.ty, FunctionType):
        (part,) = value.parts
        if finish_function is None or not isinstance(part, k.FnRef):
            raise_internal_error("CE0000", span=span, filename=filename,
                                 detail=f"the function constant {value} has no finisher")
        return finish_function(part.symbol)
    if value.variant is not None:
        return enum_initializer(value, types, finish_string, data_name, span=span,
                                filename=filename, finish_function=finish_function)

    def fault(detail: str) -> NoReturn:
        raise_internal_error("CE0000", span=span, filename=filename,
                             detail=f"the constant {value}: {detail}")

    _check_constant(value, fault)
    fields: List[ir.Constant] = []
    for index, part in enumerate(value.parts):
        name = f"{data_name}.{index}"
        if isinstance(part, k.Aggregate):
            fields.append(constant_initializer(part, types, finish_string, name, span=span,
                                               filename=filename,
                                               finish_function=finish_function))
        elif isinstance(part, k.Str):
            if finish_string is None:
                fault("a string field has no finisher")
            fields.append(finish_string(part.value.decode("utf-8", "surrogatepass"), name))
        else:
            fields.append(scalar_initializer(part, types, fault))
    return ir.Constant(types.ll_type(value.ty), fields)


def scalar_initializer(constant: k.Constant, types, fault: Fault) -> ir.Constant:
    """The LLVM constant of a scalar: an integer at the width of its type (its bits), a
    float, a `bool` and `~`."""
    if isinstance(constant, k.Int):
        ll = types.ll_type(constant.ty)
        return ir.Constant(ll, constant.value & ((1 << ll.width) - 1))
    if isinstance(constant, k.Float):
        return ir.Constant(types.ll_type(constant.ty), float(constant.value))
    if isinstance(constant, k.Bool):
        return ir.Constant(types.ll_type(BuiltinType.BOOL), int(constant.value))
    if isinstance(constant, k.Blank):
        return ir.Constant(types.ll_type(BuiltinType.BLANK), 0)
    fault(f"the constant {constant} is no scalar")


def enum_initializer(value: k.Aggregate, types,
                     finish_string: Optional[StringFinisher],
                     data_name: str, *, span: Optional[Span] = None,
                     filename: Optional[str] = None,
                     finish_function: Optional[FunctionFinisher] = None) -> ir.Constant:
    """The `{i32 tag, [K x i64] data}` constant for an enum variant constant. A value that
    the check of its variants refuses, or that the converter cannot write, is an internal
    error at `span`: a string payload with no finisher is one."""
    def fault(detail: str) -> NoReturn:
        raise_internal_error("CE0000", span=span, filename=filename,
                             detail=f"the enum constant {value}: {detail}")

    if value.variant is None:
        fault("it names no variant")
    _check_constant(value, fault)
    enum_type, variant = variant_of(value.ty, value.variant, fault)
    llvm_enum = types.get_enum_type(enum_type)
    data_type = llvm_enum.elements[1]
    tag = enum_type.get_variant_index(value.variant)
    if not value.parts:
        data = ir.Constant(data_type, None)
    else:
        blob = _PayloadBytes(data_type.count * WORD_BYTES)
        if not _write_fields(blob, 0, variant.associated_types, value.parts, types,
                             finish_string, data_name, finish_function):
            fault("a payload value has no constant bytes")
        data = ir.Constant(data_type, blob.words(types.i64))
    return ir.Constant(llvm_enum, [ir.Constant(types.i32, tag), data])


def _write_fields(blob: _PayloadBytes, base: int, field_types: Sequence[Type],
                  values: Sequence[k.Constant], types,
                  finish_string: Optional[StringFinisher], data_name: str,
                  finish_function: Optional[FunctionFinisher]) -> bool:
    """Write a naturally aligned field sequence -- a payload, or a struct's fields."""
    if len(values) != len(field_types):
        return False
    offsets = types.payload_field_offsets(list(field_types))
    for index, (field_type, field_value, offset) in enumerate(
            zip(field_types, values, offsets, strict=True)):
        if not _write(blob, base + offset, field_type, field_value, types, finish_string,
                      f"{data_name}.{index}", finish_function):
            return False
    return True


def _write(blob: _PayloadBytes, offset: int, ty: Type, value: k.Constant, types,
           finish_string: Optional[StringFinisher], data_name: str,
           finish_function: Optional[FunctionFinisher]) -> bool:
    """Write one constant value of the type `ty` at `offset`; False when it needs a module
    and has none, or when the value is no value of `ty`. A function value fills its first
    word with a pointer, and its other three words stay null."""
    if isinstance(value, k.Aggregate) and isinstance(value.ty, FunctionType):
        part = value.parts[0]
        if finish_function is None or not isinstance(part, k.FnRef):
            return False
        blob.pointers[offset // WORD_BYTES] = finish_function(part.symbol).constant[0]
        return True

    if isinstance(value, k.Aggregate):
        if isinstance(ty, StructType):
            return _write_fields(blob, offset,
                                 [field_type for _name, field_type in ty.fields],
                                 value.parts, types, finish_string, data_name, finish_function)

        if isinstance(ty, EnumType) and value.variant is not None:
            tag = ty.get_variant_index(value.variant)
            variant = ty.get_variant(value.variant)
            if tag is None or variant is None:
                return False
            blob.bytes[offset:offset + 4] = tag.to_bytes(4, "little")
            if not value.parts:
                return True
            return _write_fields(blob, offset + ENUM_TAG_SIZE_BYTES,
                                 variant.associated_types, value.parts, types,
                                 finish_string, data_name, finish_function)

        if isinstance(ty, ArrayType):
            stride = types.get_type_size_bytes(ty.base_type)
            for index, element in enumerate(value.parts):
                if not _write(blob, offset + index * stride, ty.base_type, element, types,
                              finish_string, f"{data_name}.{index}", finish_function):
                    return False
            return True

        return False

    if isinstance(value, k.Bool) and ty == BuiltinType.BOOL:
        blob.bytes[offset] = 1 if value.value else 0
        return True

    if isinstance(value, k.Int) and ty in _INTEGER_TYPES:
        size = types.get_type_size_bytes(ty)
        blob.bytes[offset:offset + size] = (value.value % (1 << (8 * size))).to_bytes(
            size, "little")
        return True

    if isinstance(value, k.Float) and ty == BuiltinType.F32:
        blob.bytes[offset:offset + 4] = struct.pack("<f", value.value)
        return True

    if isinstance(value, k.Float) and ty == BuiltinType.F64:
        blob.bytes[offset:offset + 8] = struct.pack("<d", value.value)
        return True

    if isinstance(value, k.Str) and ty == BuiltinType.STRING:
        if finish_string is None:
            return False
        text = value.value.decode("utf-8", "surrogatepass")
        data_pointer, size, owned = finish_string(text, data_name).constant
        blob.pointers[offset // WORD_BYTES] = data_pointer
        size_at, size_bytes = offset + SIZE_OFFSET, SIZE_TYPE.width // 8
        blob.bytes[size_at:size_at + size_bytes] = size.constant.to_bytes(size_bytes, "little")
        blob.bytes[offset + OWNED_OFFSET] = owned.constant
        return True

    return False
