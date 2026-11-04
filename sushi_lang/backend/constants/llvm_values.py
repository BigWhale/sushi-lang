"""LLVM IR constant value creation utilities."""
from __future__ import annotations

from llvmlite import ir
from sushi_lang.backend.constants.bit_widths import (
    INT8_BIT_WIDTH,
    INT32_BIT_WIDTH,
    INT64_BIT_WIDTH,
)

ZERO_I8 = ir.Constant(ir.IntType(INT8_BIT_WIDTH), 0)

ZERO_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 0)
ONE_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 1)
TWO_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 2)


def make_i8_const(value: int) -> ir.Constant:
    """Create an i8 constant."""
    return ir.Constant(ir.IntType(INT8_BIT_WIDTH), value)


def make_i32_const(value: int) -> ir.Constant:
    """Create an i32 constant."""
    return ir.Constant(ir.IntType(INT32_BIT_WIDTH), value)


def make_i64_const(value: int) -> ir.Constant:
    """Create an i64 constant."""
    return ir.Constant(ir.IntType(INT64_BIT_WIDTH), value)


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
