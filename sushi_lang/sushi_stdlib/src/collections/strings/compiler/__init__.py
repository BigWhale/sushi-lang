"""Inline string operations emitted during compilation."""

from .strlen import emit_strlen_intrinsic
from .is_empty import emit_string_is_empty_intrinsic

__all__ = [
    'emit_strlen_intrinsic',
    'emit_string_is_empty_intrinsic',
]
