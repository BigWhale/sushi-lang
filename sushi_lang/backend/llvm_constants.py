"""LLVM IR constant utilities (legacy compatibility module).

DEPRECATED: This module is maintained for backward compatibility only.
New code should import from sushi_lang.backend.constants.llvm_values directly.

This module re-exports from sushi_lang.backend.constants.llvm_values.
"""

# Re-export everything from the new modular structure
from sushi_lang.backend.constants.llvm_values import *
