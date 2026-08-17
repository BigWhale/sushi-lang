"""Memory management system for the Sushi compiler."""
from sushi_lang.backend.memory.scopes import ScopeManager
from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager
from sushi_lang.backend.memory.heap import emit_malloc, emit_free

__all__ = ['ScopeManager', 'DynamicArrayManager', 'emit_malloc', 'emit_free']
