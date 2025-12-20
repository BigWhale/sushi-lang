"""
Memory management system for the Sushi compiler.

This package provides modular memory management including:
- Variable scoping and alloca tracking (scopes.py)
- Dynamic array RAII and heap allocation (dynamic_arrays.py)
- Heap operations with error handling (heap.py)
"""
from sushi_lang.backend.memory.scopes import ScopeManager
from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager
from sushi_lang.backend.memory.heap import emit_malloc, emit_free

__all__ = ['ScopeManager', 'DynamicArrayManager', 'emit_malloc', 'emit_free']
