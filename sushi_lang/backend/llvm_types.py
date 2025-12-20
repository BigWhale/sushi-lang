"""
LLVM type system management for the Sushi language compiler.

This module re-exports the unified LLVMTypeSystem facade from backend/types/core
for backward compatibility. New code should import directly from sushi_lang.backend.types.core.

REFACTORING COMPLETED (Issue #1):
- TypeCache: Extracted to backend/types/core/caching.py (struct/enum caching)
- TypeSizing: Extracted to backend/types/core/sizing.py (size/alignment calculation)
- TypeMapper: Extracted to backend/types/core/mapping.py (type mapping logic)
- TypeInference: Extracted to backend/types/core/inference.py (LLVM->Sushi inference)
- Facade pattern: Implemented in backend/types/core/__init__.py

File reduced from 648 lines to ~40 lines (94% reduction).
All type system logic now properly separated by concern.
"""
from __future__ import annotations

from sushi_lang.backend.types.core import LLVMTypeSystem


class TypeSystemWrapper:
    """Minimal codegen wrapper for generic type helpers.

    Used by HashMap and List type extraction functions that need access
    to the type system and tables but don't need full codegen functionality.
    """
    def __init__(self, types_system, struct_table, enum_table):
        self.types = types_system
        self.struct_table = struct_table
        self.enum_table = enum_table


# Re-export LLVMTypeSystem for backward compatibility
__all__ = ['LLVMTypeSystem', 'TypeSystemWrapper']
