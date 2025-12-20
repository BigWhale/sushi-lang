"""
String Modification Operations

Facade module that re-exports all string modification methods for backward compatibility.
"""

from .replace import emit_string_replace
from .reverse import emit_string_reverse
from .pad import emit_string_repeat, emit_string_pad_left, emit_string_pad_right
from .trim import emit_string_strip_prefix, emit_string_strip_suffix

__all__ = [
    'emit_string_replace',
    'emit_string_reverse',
    'emit_string_repeat',
    'emit_string_pad_left',
    'emit_string_pad_right',
    'emit_string_strip_prefix',
    'emit_string_strip_suffix',
]
