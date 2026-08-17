"""Inline string operations emitted during compilation."""

from .strcmp import emit_strcmp_intrinsic
from .strlen import emit_strlen_intrinsic
from .is_empty import emit_string_is_empty_intrinsic

__all__ = [
    'emit_strcmp_intrinsic',
    'emit_strlen_intrinsic',
    'emit_string_is_empty_intrinsic',
]
