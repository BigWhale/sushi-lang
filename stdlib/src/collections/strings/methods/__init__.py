"""
User-facing string methods compiled into stdlib .bc file.

These methods are precompiled into stdlib/dist/collections/strings.bc
and require `use <collections/strings>` to use.
"""

from .basic import (
    emit_string_size,
    emit_string_len,
    emit_string_concat,
)
from .slice import (
    emit_string_ss,
    emit_string_sleft,
    emit_string_sright,
    emit_string_char_at,
    emit_string_s,
)
from .search import (
    emit_string_starts_with,
    emit_string_ends_with,
    emit_string_contains,
    emit_string_find,
)
from .trim import (
    emit_string_trim,
    emit_string_tleft,
    emit_string_tright,
)
from .case import (
    emit_string_upper,
    emit_string_lower,
    emit_string_cap,
)
from .convert import (
    emit_string_to_bytes,
    emit_string_split,
)
from .modify import (
    emit_string_replace,
)
from .parse import (
    emit_string_to_i32,
    emit_string_to_i64,
    emit_string_to_f64,
)

__all__ = [
    'emit_string_size',
    'emit_string_len',
    'emit_string_concat',
    'emit_string_ss',
    'emit_string_sleft',
    'emit_string_sright',
    'emit_string_char_at',
    'emit_string_s',
    'emit_string_starts_with',
    'emit_string_ends_with',
    'emit_string_contains',
    'emit_string_find',
    'emit_string_trim',
    'emit_string_tleft',
    'emit_string_tright',
    'emit_string_upper',
    'emit_string_lower',
    'emit_string_cap',
    'emit_string_to_bytes',
    'emit_string_split',
    'emit_string_replace',
    'emit_string_to_i32',
    'emit_string_to_i64',
    'emit_string_to_f64',
]
