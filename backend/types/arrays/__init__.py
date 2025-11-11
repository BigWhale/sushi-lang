"""
Array operations for the Sushi language compiler.

This module handles LLVM IR emission for both fixed and dynamic arrays, including:
- Array literal emission (fixed-size arrays)
- Array indexing with bounds checking (runtime error RE2020)
- Dynamic array constructors (new, from)
- Dynamic array methods (len, capacity, get, push, pop, destroy)
- Dynamic array helper utilities
- Array method dispatching

All dynamic array operations use exponential growth strategy and include
comprehensive bounds checking for safety.

Submodules:
- literals: Fixed-size array literal emission
- indexing: Array element access with bounds checking
- constructors: Dynamic array construction (new, from)
- methods: Dynamic array operations (len, capacity, get, push, pop, destroy)
- iterators: Array iterator emission for foreach loops
- dispatcher: Central dispatch for array method calls
- utils: Helper functions for array struct creation

Architecture:
Arrays are a CORE LANGUAGE FEATURE (not stdlib). They use fully generic inline
emission at compile time, which works for ANY element type (primitives, strings,
structs, enums). This avoids the need for monomorphization and pre-compiling
separate functions for each array type.
"""
from __future__ import annotations

# Import all public functions for convenience
from .literals import emit_array_literal
from .indexing import emit_index_access
from .methods.core import (
    emit_dynamic_array_new,
    emit_dynamic_array_from,
    emit_dynamic_array_len,
    emit_dynamic_array_capacity,
    emit_dynamic_array_get,
    emit_dynamic_array_push,
    emit_dynamic_array_pop,
    emit_dynamic_array_destroy,
)
from .utils import create_dynamic_array_from_elements
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
    'emit_dynamic_array_get',
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
]
