"""
Built-in extension methods for HashMap<K, V> generic struct.

INLINE EMISSION ONLY. HashMap<K, V> methods work on-demand for all types.

There is no stdlib IR generation because monomorphizing for all possible user
key/value type combinations is impractical. HashMap<K, V> must support any
types K and V that users define (custom structs, nested generics, etc.).
Pre-generating all possible instantiations is not feasible.

Implemented methods:
- new() -> HashMap<K, V>: Create empty map (16 initial buckets)
- insert(K, V) -> ~: Add/update entry (auto-resizes when load > 0.75)
- get(K) -> Maybe<V>: Retrieve value by key
- contains_key(K) -> bool: Check if key exists
- remove(K) -> Maybe<V>: Delete entry (marks tombstone)
- len() -> i32: Count occupied entries (excludes tombstones)
- is_empty() -> bool: Check if size == 0
- tombstone_count() -> i32: Count deleted slots
- rehash() -> ~: Rebuild without tombstones (same capacity)
- free() -> ~: Recursively destroy all entries, reset to initial capacity (still usable)
- destroy() -> ~: Recursively destroy all entries, set buckets to null (unusable after)
- debug() -> ~: Print internal state for debugging
- keys() -> Iterator<K>: Create iterator over keys (for foreach loops)
- values() -> Iterator<V>: Create iterator over values (for foreach loops)

The HashMap<K, V> type is a generic struct with open addressing:
- Internal Entry<K, V> structure (not exposed):
  - K key
  - V value
  - u8 state (0=Empty, 1=Occupied, 2=Tombstone)
- Uses linear probing for collision resolution
- Power-of-two capacities for fast bitwise AND indexing
- Automatic resize: doubles capacity when (size + tombstones) / capacity > 0.75
- Resize clears all tombstones and rehashes all entries
- Initial capacity: 16, grows to 32, 64, 128, etc.

This module provides hash table methods that work with HashMap<K, V> after
monomorphization.
"""

from typing import Any
from semantics.ast import MethodCall
from semantics.typesys import StructType
import llvmlite.ir as ir

# Public API - validation
from .validation import (
    is_builtin_hashmap_method,
    validate_hashmap_method_with_validator
)
from internals.errors import raise_internal_error

# Public API - LLVM emission
from .methods.core import (
    emit_hashmap_new,
    emit_hashmap_len,
    emit_hashmap_is_empty,
    emit_hashmap_tombstone_count,
    emit_hashmap_get,
    emit_hashmap_contains_key
)
from .methods.mutations import (
    emit_hashmap_insert,
    emit_hashmap_remove,
    emit_hashmap_rehash,
    emit_hashmap_free,
    emit_hashmap_destroy
)
from .methods.debug import emit_hashmap_debug
from .methods.iterators import emit_hashmap_keys, emit_hashmap_values


def emit_hashmap_method(
    codegen: Any,
    expr: MethodCall,
    receiver_value: ir.Value,
    receiver_type: StructType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM IR for HashMap<K, V> method calls.

    HashMap<K, V> is a CORE language feature that uses inline emission.
    This function dispatches to specialized emitters based on method name.

    Args:
        codegen: The LLVM code generator.
        expr: The method call expression.
        receiver_value: The LLVM value of the HashMap (None for new()).
        receiver_type: The HashMap<K, V> struct type.
        to_i1: Whether to convert result to i1.

    Returns:
        The result of the HashMap method call.

    Raises:
        ValueError: If method name is not recognized.
    """
    method = expr.method

    # Dispatch to method-specific emitters
    if method == "new":
        result = emit_hashmap_new(codegen, receiver_type)
    elif method == "insert":
        result = emit_hashmap_insert(codegen, expr, receiver_value, receiver_type)
    elif method == "get":
        result = emit_hashmap_get(codegen, expr, receiver_value, receiver_type)
    elif method == "contains_key":
        result = emit_hashmap_contains_key(codegen, expr, receiver_value, receiver_type)
    elif method == "remove":
        result = emit_hashmap_remove(codegen, expr, receiver_value, receiver_type)
    elif method == "rehash":
        result = emit_hashmap_rehash(codegen, receiver_value, receiver_type)
    elif method == "free":
        result = emit_hashmap_free(codegen, receiver_value, receiver_type)
    elif method == "destroy":
        result = emit_hashmap_destroy(codegen, receiver_value, receiver_type)
    elif method == "debug":
        result = emit_hashmap_debug(codegen, receiver_value, receiver_type)
    elif method == "len":
        result = emit_hashmap_len(codegen, receiver_value)
    elif method == "is_empty":
        result = emit_hashmap_is_empty(codegen, receiver_value)
    elif method == "tombstone_count":
        result = emit_hashmap_tombstone_count(codegen, receiver_value)
    elif method == "keys":
        result = emit_hashmap_keys(codegen, expr, receiver_value, receiver_type)
    elif method == "values":
        result = emit_hashmap_values(codegen, expr, receiver_value, receiver_type)
    else:
        raise_internal_error("CE0085", method=method)

    # Convert to i1 if needed
    if to_i1 and method in ("is_empty", "contains_key"):
        result = codegen.utils.as_i1(result)

    return result


__all__ = [
    # Validation
    'is_builtin_hashmap_method',
    'validate_hashmap_method_with_validator',
    # Emission entry point
    'emit_hashmap_method',
    # Individual method emitters (for advanced use)
    'emit_hashmap_new',
    'emit_hashmap_insert',
    'emit_hashmap_get',
    'emit_hashmap_contains_key',
    'emit_hashmap_remove',
    'emit_hashmap_len',
    'emit_hashmap_is_empty',
    'emit_hashmap_tombstone_count',
    'emit_hashmap_rehash',
    'emit_hashmap_free',
    'emit_hashmap_destroy',
    'emit_hashmap_debug',
    'emit_hashmap_keys',
    'emit_hashmap_values',
]
