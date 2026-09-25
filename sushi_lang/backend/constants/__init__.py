"""Backend constants facade: the names that callers import from the package itself."""

from sushi_lang.backend.constants.bit_widths import (
    INT8_BIT_WIDTH,
    INT32_BIT_WIDTH,
    INT64_BIT_WIDTH,
)

from sushi_lang.backend.constants.llvm_values import (
    HASHMAP_BUCKETS_INDICES,
    HASHMAP_SIZE_INDICES,
    HASHMAP_CAPACITY_INDICES,
    HASHMAP_TOMBSTONES_INDICES,
    BUCKETS_DATA_INDICES,
    ENTRY_KEY_INDICES,
    ENTRY_VALUE_INDICES,
    ENTRY_STATE_INDICES,
)

from sushi_lang.backend.constants.sizes import (
    FAT_POINTER_SIZE_BYTES,
    DYNAMIC_ARRAY_SIZE_BYTES,
    ITERATOR_SIZE_BYTES,
    ENUM_TAG_SIZE_BYTES,
)

__all__ = [
    'INT8_BIT_WIDTH',
    'INT32_BIT_WIDTH',
    'INT64_BIT_WIDTH',
    'HASHMAP_BUCKETS_INDICES',
    'HASHMAP_SIZE_INDICES',
    'HASHMAP_CAPACITY_INDICES',
    'HASHMAP_TOMBSTONES_INDICES',
    'BUCKETS_DATA_INDICES',
    'ENTRY_KEY_INDICES',
    'ENTRY_VALUE_INDICES',
    'ENTRY_STATE_INDICES',
    'FAT_POINTER_SIZE_BYTES',
    'DYNAMIC_ARRAY_SIZE_BYTES',
    'ITERATOR_SIZE_BYTES',
    'ENUM_TAG_SIZE_BYTES',
]
