"""Backend utilities package.

This package contains reusable utility functions for the LLVM backend,
organized by concern:

- validation: Common precondition checks (builder, function, non-empty)
"""

from .validation import (
    require_both_initialized,
    require_builder,
    require_function,
    require_non_empty,
)

__all__ = [
    'require_builder',
    'require_function',
    'require_non_empty',
    'require_both_initialized',
]
