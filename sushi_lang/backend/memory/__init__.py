"""The heap and the stack slots of the backend."""
from sushi_lang.backend.memory.heap import emit_malloc, emit_free

__all__ = ['emit_malloc', 'emit_free']
