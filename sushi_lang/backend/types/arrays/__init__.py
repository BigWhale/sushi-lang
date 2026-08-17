"""Array operations for the Sushi language compiler."""
from __future__ import annotations

# Import all public functions for convenience
from .literals import emit_array_literal
from .indexing import emit_index_access
from .methods.core import (
    emit_dynamic_array_new,
    emit_dynamic_array_from,
    emit_dynamic_array_len,
    emit_dynamic_array_capacity,
    emit_dynamic_array_push,
    emit_dynamic_array_pop,
    emit_dynamic_array_destroy,
)
from .utils import create_dynamic_array_from_elements, emit_array_literal_elements
from .methods.iterators import emit_fixed_array_iter, emit_dynamic_array_iter
from .methods.transforms import emit_dynamic_array_clone, emit_byte_array_to_string
from .methods.hashing import emit_fixed_array_hash_direct, emit_dynamic_array_hash_direct
from .dispatcher import is_builtin_array_method, emit_array_method

__all__ = [
    # Literals
    'emit_array_literal',
    # Indexing
    'emit_index_access',
    # Constructors
    'emit_dynamic_array_new',
    'emit_dynamic_array_from',
    # Methods
    'emit_dynamic_array_len',
    'emit_dynamic_array_capacity',
    'emit_dynamic_array_push',
    'emit_dynamic_array_pop',
    'emit_dynamic_array_destroy',
    # Iterators
    'emit_fixed_array_iter',
    'emit_dynamic_array_iter',
    # Clone and convert
    'emit_dynamic_array_clone',
    'emit_byte_array_to_string',
    # Hashing
    'emit_fixed_array_hash_direct',
    'emit_dynamic_array_hash_direct',
    # Dispatcher
    'is_builtin_array_method',
    'emit_array_method',
    # Utils
    'create_dynamic_array_from_elements',
    'emit_array_literal_elements',
]
