"""
Inline string operations emitted during compilation.

These functions are emitted directly into the compiled module
rather than loaded from precompiled stdlib .bc files.

Used for:
- strcmp: HashMap<string, V> key comparison
- strlen: C string to fat pointer conversion
- is_empty: String emptiness checks (no stdlib import required)
"""

from .strcmp import emit_strcmp_intrinsic
from .strlen import emit_strlen_intrinsic
from .is_empty import emit_string_is_empty_intrinsic

__all__ = [
    'emit_strcmp_intrinsic',
    'emit_strlen_intrinsic',
    'emit_string_is_empty_intrinsic',
]
