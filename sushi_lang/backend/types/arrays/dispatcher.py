"""The array method family: one emitter for each method of a `T[N]` and a `T[]` (ruling Q7).

The SLIR emitter evaluates every operand before the family runs (docs/design/ir.md 11.9):
the receiver gives the ADDRESS of its storage, and each argument gives its value. So each
arm reads the state of the receiver (its length, its capacity, its data) after the last
argument, when it runs. One emitter serves both kinds of array: the arm reads the data and
the length of either kind through `array_data` and `array_length`. A method that only a
`T[]` takes reads the descriptor through `_descriptor`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from llvmlite import ir
from sushi_lang.backend.string_layout import string_size

from sushi_lang.backend.utils.validation import require_builder
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, Type

from .addressing import as_array_address
from .fixed_addressing import as_fixed_array_address
from .indexing import array_data, array_length
from .methods import core, reductions, search
from .methods.range_eq import emit_array_eq_range
from .methods.safe_access import emit_array_get_maybe

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

#: The value of an argument, and its semantic type.
Argument = tuple[ir.Value, Type]


def emit_array_method(codegen: 'LLVMCodegen', name: str, receiver: ir.Value,
                      array_type: ArrayType | DynamicArrayType,
                      args: Sequence[Argument]) -> ir.Value:
    """The value of the method `name` on the array at the address `receiver`. A name that
    the family does not emit is an internal error."""
    element = array_type.base_type
    builder = require_builder(codegen)
    zero = ir.Constant(codegen.types.i32, 0)

    def state() -> tuple[ir.Value, ir.Value]:
        return array_data(codegen, receiver, array_type), array_length(codegen, receiver,
                                                                       array_type)

    match name:
        case "len":
            return array_length(codegen, receiver, array_type)

        case "capacity":
            return core.emit_dynamic_array_capacity(codegen, _descriptor(receiver, array_type,
                                                                         name))

        case "get":
            data, count = state()
            return emit_array_get_maybe(codegen, data, count, _index(codegen, args[0]),
                                        element)

        case "first" | "last":
            # `get()` with the index built in: 0, or len - 1. An empty array gives the last
            # index -1, which the bounds check turns into `Maybe.None()`.
            data, count = state()
            index = (zero if name == "first"
                     else builder.sub(count, ir.Constant(codegen.types.i32, 1),
                                      name="last_index"))
            return emit_array_get_maybe(codegen, data, count, index, element)

        case "contains" | "index_of" | "index_of_from":
            needle = args[0][0]
            data, count = state()
            if name == "contains":
                return search.emit_array_contains(codegen, data, count, needle, element,
                                                  to_i1=False)
            if name == "index_of_from":
                return search.emit_array_index_of_from(codegen, data, count, needle,
                                                       _clamped(codegen, args[1]), element)
            return search.emit_array_index_of(codegen, data, count, needle, element)

        case "starts_with" | "eq_range":
            # `starts_with(prefix)` is `eq_range(0, prefix)`.
            start = zero if name == "starts_with" else _clamped(codegen, args[0])
            other_data, other_len = _source(codegen, args[-1])
            data, count = state()
            return emit_array_eq_range(codegen, data, count, start, other_data, other_len,
                                       element)

        case "push":
            descriptor = _descriptor(receiver, array_type, name)
            return core.emit_dynamic_array_push(codegen, descriptor,
                                                _struct_type(codegen, array_type), args[0][0])

        case "insert":
            from sushi_lang.backend.generics.container_shift import emit_descriptor_insert
            descriptor = _descriptor(receiver, array_type, name)
            return emit_descriptor_insert(codegen, descriptor, _index(codegen, args[0]),
                                          args[1][0], element, codegen.types.ll_type(element))

        case "remove":
            from sushi_lang.backend.generics.container_shift import emit_descriptor_remove
            descriptor = _descriptor(receiver, array_type, name)
            return emit_descriptor_remove(codegen, descriptor, _index(codegen, args[0]),
                                          element, codegen.types.ll_type(element))

        case "pop":
            return core.emit_dynamic_array_pop(codegen, _descriptor(receiver, array_type, name),
                                               element)

        case "clear" | "truncate" | "resize":
            # Only `resize` has a fill value, a borrow: each added slot takes its own copy.
            new_len = (zero if name == "clear"
                       else codegen.utils.count_as_i32(args[0][0]) if name == "resize"
                       else _clamped(codegen, args[0]))
            fill = args[1][0] if name == "resize" else None
            return core.emit_dynamic_array_resize(codegen,
                                                  _descriptor(receiver, array_type, name),
                                                  _struct_type(codegen, array_type),
                                                  new_len, fill, element)

        case "free":
            return core.emit_dynamic_array_free(codegen,
                                                _descriptor(receiver, array_type, name),
                                                _struct_type(codegen, array_type), element)

        case "destroy":
            return core.emit_dynamic_array_destroy(codegen,
                                                   _descriptor(receiver, array_type, name),
                                                   _struct_type(codegen, array_type),
                                                   array_type)

        case "clone":
            from sushi_lang.backend.ownership import copy_out
            return copy_out(codegen, receiver, array_type)

        case "to_string" | "to_string_checked":
            from .methods import transforms
            descriptor = _descriptor(receiver, array_type, name)
            if name == "to_string":
                return transforms.emit_byte_array_to_string(codegen, descriptor)
            return transforms.emit_byte_array_to_string_checked(codegen, descriptor)

        case "fill":
            # A borrow: each slot takes its own copy.
            data, count = state()
            return core.emit_array_fill(codegen, data, count, args[0][0], element)

        case "reverse":
            data, count = state()
            return core.emit_array_reverse(codegen, data, count)

        case "swap":
            first, second = _index(codegen, args[0]), _index(codegen, args[1])
            data, count = state()
            return core.emit_array_swap(codegen, data, count, first, second)

        case "min" | "max":
            data, count = state()
            return reductions.emit_array_extreme(codegen, data, count, element, name)

        case "add_up":
            data, count = state()
            return reductions.emit_array_add_up(codegen, data, count, element)

        case "extend" | "extend_range":
            descriptor = _descriptor(receiver, array_type, name)
            source_data, source_len = _source(codegen, args[0])
            if name == "extend_range":
                start, count = _clamped(codegen, args[1]), _clamped(codegen, args[2])
            else:
                start, count = zero, source_len
            return core.emit_dynamic_array_extend(codegen, descriptor,
                                                  _struct_type(codegen, array_type),
                                                  source_data, source_len, start, count,
                                                  element)

        case "extend_str":
            descriptor = _descriptor(receiver, array_type, name)
            text = args[0][0]
            text_data = builder.extract_value(text, 0, name="extend_str_data")
            text_size = string_size(builder, text, name="extend_str_size")
            return core.emit_dynamic_array_extend(codegen, descriptor,
                                                  _struct_type(codegen, array_type),
                                                  text_data, text_size, zero, text_size,
                                                  element)

        case "extend_back":
            from .copy import emit_dynamic_array_extend_back
            descriptor = _descriptor(receiver, array_type, name)
            return emit_dynamic_array_extend_back(
                codegen, descriptor, codegen.types.ll_type(element),
                _clamped(codegen, args[0]), _clamped(codegen, args[1]), element)

        case "s" | "ss":
            # Two spellings of one copy: `.s` takes an exclusive END, `.ss` a LENGTH. The
            # bounds go to `clamp_range` as written, which clamps the start first.
            start, extent = _clamped(codegen, args[0]), _clamped(codegen, args[1])
            data, count = state()
            return core.emit_dynamic_array_slice(
                codegen, codegen.types.ll_type(element), data, count, start, extent,
                element, extent_is_end=name == "s")

    raise_internal_error("CE0024", method=name, type=display_type(array_type))


def _index(codegen: 'LLVMCodegen', arg: Argument) -> ir.Value:
    """An index that a bounds check reads: the i64 of the index position, which the check
    compares at 64 bits (docs/adr/0001)."""
    return codegen.utils.require_index(arg[0])


def _clamped(codegen: 'LLVMCodegen', arg: Argument) -> ir.Value:
    """A count, a start or a bound that clamps: the i64 of the index position, saturated
    to i32 (`index_as_i32`)."""
    return codegen.utils.index_as_i32(arg[0])


def _descriptor(receiver: ir.Value, array_type: Type, name: str) -> ir.Value:
    """The address of the descriptor of a `T[]` receiver. The typecheck pass refuses a
    method that changes the length of a `T[N]` (CE2023), so a fixed one here is an
    internal error."""
    if not isinstance(array_type, DynamicArrayType):
        raise_internal_error("CE0024", method=name, type=display_type(array_type))
    return receiver


def _struct_type(codegen: 'LLVMCodegen', array_type: Type) -> ir.Type:
    return codegen.types.ll_type(array_type)


def _source(codegen: 'LLVMCodegen', arg: Argument) -> tuple[ir.Value, ir.Value]:
    """The data and the length of a borrowed array argument of either kind: the source of
    a bulk copy, and the other array of a range comparison."""
    value, ty = arg
    if isinstance(ty, ArrayType):
        address = as_fixed_array_address(codegen, value)
    elif isinstance(ty, DynamicArrayType):
        address = as_array_address(codegen, value)
    else:
        raise_internal_error("CE0042", type=type(ty).__name__)
    return array_data(codegen, address, ty), array_length(codegen, address, ty)
