"""LLVM IR constant value creation utilities."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.backend.constants.bit_widths import (
    INT8_BIT_WIDTH,
    INT16_BIT_WIDTH,
    INT32_BIT_WIDTH,
    INT64_BIT_WIDTH,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.const_eval import ConstantValue


FALSE_I1 = ir.Constant(ir.IntType(1), 0)
TRUE_I1 = ir.Constant(ir.IntType(1), 1)

ZERO_I8 = ir.Constant(ir.IntType(INT8_BIT_WIDTH), 0)
ONE_I8 = ir.Constant(ir.IntType(INT8_BIT_WIDTH), 1)

ZERO_I16 = ir.Constant(ir.IntType(INT16_BIT_WIDTH), 0)
ONE_I16 = ir.Constant(ir.IntType(INT16_BIT_WIDTH), 1)

ZERO_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 0)
ONE_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 1)
TWO_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 2)

ZERO_I64 = ir.Constant(ir.IntType(INT64_BIT_WIDTH), 0)
ONE_I64 = ir.Constant(ir.IntType(INT64_BIT_WIDTH), 1)


def make_i8_const(value: int) -> ir.Constant:
    """Create an i8 constant."""
    return ir.Constant(ir.IntType(INT8_BIT_WIDTH), value)


def make_i16_const(value: int) -> ir.Constant:
    """Create an i16 constant."""
    return ir.Constant(ir.IntType(INT16_BIT_WIDTH), value)


def make_i32_const(value: int) -> ir.Constant:
    """Create an i32 constant."""
    return ir.Constant(ir.IntType(INT32_BIT_WIDTH), value)


def make_i64_const(value: int) -> ir.Constant:
    """Create an i64 constant."""
    return ir.Constant(ir.IntType(INT64_BIT_WIDTH), value)


def make_bool_const(value: bool) -> ir.Constant:
    """Create an i1 boolean constant."""
    return TRUE_I1 if value else FALSE_I1


def make_int_const(bit_width: int, value: int) -> ir.Constant:
    """Create an integer constant of arbitrary bit width."""
    return ir.Constant(ir.IntType(bit_width), value)


def gep_indices_struct(field_index: int) -> list[ir.Constant]:
    """Create GEP indices for struct field access: [0, field_index]."""
    return [ZERO_I32, make_i32_const(field_index)]


LIST_LEN_INDICES = [ZERO_I32, ZERO_I32]     # List.len field (index 0)
LIST_CAP_INDICES = [ZERO_I32, ONE_I32]      # List.cap field (index 1)
LIST_DATA_INDICES = [ZERO_I32, TWO_I32]     # List.data field (index 2)

# For HashMap<K, V> fields: {buckets, size, capacity, tombstones}.
# `buckets` is itself a dynamic array {len, cap, data}, so reaching the bucket
# storage is a second GEP through BUCKETS_DATA_INDICES.
HASHMAP_BUCKETS_INDICES = [ZERO_I32, ZERO_I32]              # HashMap.buckets (index 0)
HASHMAP_SIZE_INDICES = [ZERO_I32, ONE_I32]                  # HashMap.size (index 1)
HASHMAP_CAPACITY_INDICES = [ZERO_I32, TWO_I32]              # HashMap.capacity (index 2)
HASHMAP_TOMBSTONES_INDICES = [ZERO_I32, make_i32_const(3)]  # HashMap.tombstones (index 3)
BUCKETS_DATA_INDICES = [ZERO_I32, TWO_I32]                  # buckets.data (index 2)

# For the INTERNAL Entry<K, V>: {key, value, state}.
# Do NOT use ENTRY_STATE_INDICES on the user-facing Entry<K, V> returned by
# .entries() -- that one has only {key, value}, and index 2 is out of bounds.
ENTRY_KEY_INDICES = [ZERO_I32, ZERO_I32]    # Entry.key (index 0)
ENTRY_VALUE_INDICES = [ZERO_I32, ONE_I32]   # Entry.value (index 1)
ENTRY_STATE_INDICES = [ZERO_I32, TWO_I32]   # Entry.state (index 2, internal only)


def const_value_to_llvm(value: 'ConstantValue', types) -> Optional[ir.Constant]:
    """An evaluated `ConstantValue` as an LLVM constant, or None where it needs a module.

    Lives here and not on `ConstantValue` because the evaluator is a semantic helper and
    may not name an LLVM type (IR.md Phase 0). A string returns None: its bytes need a
    module to live in, so `_materialize_constant` finishes one.
    """
    from sushi_lang.semantics.typesys import BuiltinType, StructType

    if value.semantic_type == BuiltinType.BOOL:
        return ir.Constant(types.i8, 1 if value.value else 0)
    elif value.semantic_type == BuiltinType.I8:
        return ir.Constant(types.i8, value.value)
    elif value.semantic_type == BuiltinType.I16:
        return ir.Constant(types.i16, value.value)
    elif value.semantic_type == BuiltinType.I32:
        return ir.Constant(types.i32, value.value)
    elif value.semantic_type == BuiltinType.I64:
        return ir.Constant(types.i64, value.value)
    elif value.semantic_type == BuiltinType.U8:
        return ir.Constant(types.u8, value.value)
    elif value.semantic_type == BuiltinType.U16:
        return ir.Constant(types.u16, value.value)
    elif value.semantic_type == BuiltinType.U32:
        return ir.Constant(types.u32, value.value)
    elif value.semantic_type == BuiltinType.U64:
        return ir.Constant(types.u64, value.value)
    elif value.semantic_type == BuiltinType.F32:
        return ir.Constant(types.f32, value.value)
    elif value.semantic_type == BuiltinType.F64:
        return ir.Constant(types.f64, value.value)
    elif value.semantic_type == BuiltinType.STRING:
        return None
    elif isinstance(value.semantic_type, StructType):
        # A struct is an aggregate whose fields have types of their own, so it is asked
        # before the array arm below -- that one reads `elements[0].type` for every slot.
        field_constants = [const_value_to_llvm(field, types) for field in value.value]
        if any(c is None for c in field_constants):
            return None
        return ir.Constant(types.ll_type(value.semantic_type), field_constants)
    elif isinstance(value.value, list):
        element_constants = [const_value_to_llvm(elem, types) for elem in value.value]
        if any(c is None for c in element_constants):
            return None
        element_type = element_constants[0].type
        array_type = ir.ArrayType(element_type, len(element_constants))
        return ir.Constant(array_type, element_constants)
    else:
        return None
