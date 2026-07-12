"""LLVM IR constant value creation utilities.

This module provides precomputed LLVM constants and factory functions
to eliminate code duplication across the backend.
"""

from llvmlite import ir
from sushi_lang.backend.constants.bit_widths import (
    INT8_BIT_WIDTH,
    INT16_BIT_WIDTH,
    INT32_BIT_WIDTH,
    INT64_BIT_WIDTH,
)


# === Precomputed Integer Constants ===

# Boolean (i1) constants
FALSE_I1 = ir.Constant(ir.IntType(1), 0)
TRUE_I1 = ir.Constant(ir.IntType(1), 1)

# i8 constants
ZERO_I8 = ir.Constant(ir.IntType(INT8_BIT_WIDTH), 0)
ONE_I8 = ir.Constant(ir.IntType(INT8_BIT_WIDTH), 1)

# i16 constants
ZERO_I16 = ir.Constant(ir.IntType(INT16_BIT_WIDTH), 0)
ONE_I16 = ir.Constant(ir.IntType(INT16_BIT_WIDTH), 1)

# i32 constants
ZERO_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 0)
ONE_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 1)
TWO_I32 = ir.Constant(ir.IntType(INT32_BIT_WIDTH), 2)

# i64 constants
ZERO_I64 = ir.Constant(ir.IntType(INT64_BIT_WIDTH), 0)
ONE_I64 = ir.Constant(ir.IntType(INT64_BIT_WIDTH), 1)


# === Factory Functions ===

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


# === Common GEP Index Lists ===

# For struct field access [0, field_index]
def gep_indices_struct(field_index: int) -> list[ir.Constant]:
    """Create GEP indices for struct field access: [0, field_index]."""
    return [ZERO_I32, make_i32_const(field_index)]


# For List type fields (common pattern)
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
