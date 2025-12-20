"""HashMap<K, V> method implementations.

This package contains the LLVM IR emission code for all HashMap methods.
"""

from .core import (
    emit_hashmap_new,
    emit_hashmap_len,
    emit_hashmap_is_empty,
    emit_hashmap_tombstone_count,
    emit_hashmap_get,
    emit_hashmap_contains_key
)
from .mutations import (
    emit_hashmap_insert,
    emit_hashmap_remove,
    emit_hashmap_rehash,
    emit_hashmap_free,
    emit_hashmap_destroy
)
from .debug import emit_hashmap_debug
from .iterators import emit_hashmap_keys, emit_hashmap_values

__all__ = [
    'emit_hashmap_new',
    'emit_hashmap_len',
    'emit_hashmap_is_empty',
    'emit_hashmap_tombstone_count',
    'emit_hashmap_get',
    'emit_hashmap_contains_key',
    'emit_hashmap_insert',
    'emit_hashmap_remove',
    'emit_hashmap_rehash',
    'emit_hashmap_free',
    'emit_hashmap_destroy',
    'emit_hashmap_debug',
    'emit_hashmap_keys',
    'emit_hashmap_values',
]
