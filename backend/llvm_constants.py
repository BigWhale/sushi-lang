"""LLVM IR constant utilities (legacy compatibility module).

DEPRECATED: This module is maintained for backward compatibility only.
New code should import from backend.constants.llvm_values directly.

This module re-exports from backend.constants.llvm_values.
"""

# Re-export everything from the new modular structure
from backend.constants.llvm_values import *
