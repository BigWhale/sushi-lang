"""Array operations for the Sushi language compiler."""
from __future__ import annotations

from .methods.core import (
    emit_dynamic_array_len,
    emit_dynamic_array_capacity,
    emit_dynamic_array_push,
    emit_dynamic_array_pop,
    emit_dynamic_array_destroy,
)
from .methods.iterators import emit_fixed_array_iter, emit_dynamic_array_iter
from .methods.transforms import emit_dynamic_array_clone, emit_byte_array_to_string
from .methods.hashing import emit_fixed_array_hash_direct, emit_dynamic_array_hash_direct
from .dispatcher import is_builtin_array_method, emit_array_method

__all__ = [
    'emit_dynamic_array_len',
    'emit_dynamic_array_capacity',
    'emit_dynamic_array_push',
    'emit_dynamic_array_pop',
    'emit_dynamic_array_destroy',
    'emit_fixed_array_iter',
    'emit_dynamic_array_iter',
    'emit_dynamic_array_clone',
    'emit_byte_array_to_string',
    'emit_fixed_array_hash_direct',
    'emit_dynamic_array_hash_direct',
    'is_builtin_array_method',
    'emit_array_method',
]
