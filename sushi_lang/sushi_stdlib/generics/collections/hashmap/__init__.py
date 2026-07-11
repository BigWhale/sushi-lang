"""HashMap<K, V> LLVM emission.

Implements all HashMap methods using inline LLVM IR emission. The ir-free half
(method validation, K/V resolution, the generic struct definition) lives in
`semantics/generics/hashmap.py`.

HashMap is a hash table with open addressing using linear probing.
Requires `use <collections/hashmap>` to be available.

Type Parameters:
    K: Key type (must support .hash() and equality comparison)
    V: Value type (any type)

Methods:
    Static:
        new() -> HashMap<K, V>

    Instance (read-only):
        len() -> i32
        is_empty() -> bool
        tombstone_count() -> i32
        get(K) -> Maybe<V>
        contains_key(K) -> bool
        keys() -> Iterator<K>
        values() -> Iterator<V>
        entries() -> Iterator<Entry<K, V>>
        debug() -> ~

    Instance (mutating):
        insert(K, V) -> ~
        remove(K) -> Maybe<V>
        rehash() -> ~
        free() -> ~
        destroy() -> ~
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Union


# Validation lives in semantics (semantics/generics/hashmap.py); re-exported here so the
# backend dispatcher can gate on it without reaching across packages twice.
from sushi_lang.semantics.generics.hashmap import (
    is_builtin_hashmap_method,
    validate_hashmap_method_with_validator,
)

# Public API - LLVM emission
from .methods import (
    emit_hashmap_new,
    emit_hashmap_len,
    emit_hashmap_is_empty,
    emit_hashmap_tombstone_count,
    emit_hashmap_get,
    emit_hashmap_contains_key,
    emit_hashmap_insert,
    emit_hashmap_remove,
    emit_hashmap_rehash,
    emit_hashmap_free,
    emit_hashmap_destroy,
    emit_hashmap_debug,
    emit_hashmap_keys,
    emit_hashmap_values,
    emit_hashmap_entries,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import StructType
    from sushi_lang.semantics.ast import MethodCall
    import llvmlite.ir as ir


def emit_hashmap_method(
    codegen,
    expr: 'MethodCall',
    receiver_value: Union['ir.Value', None],
    receiver_type: 'StructType',
    to_i1: bool
) -> 'ir.Value':
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
    from sushi_lang.internals.errors import raise_internal_error

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
    elif method == "entries":
        result = emit_hashmap_entries(codegen, expr, receiver_value, receiver_type)
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
    'emit_hashmap_entries',
]
