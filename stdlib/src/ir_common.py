"""
Common infrastructure for standalone LLVM IR generation in stdlib modules.

This module has been refactored. Most functionality moved to focused modules:
- libc_declarations.py: C function declarations
- string_helpers.py: String utilities
- conversions.py: Type-to-string conversions
- error_emission.py: Runtime error handling
- type_converters.py: Semantic type ↔ LLVM type conversion
- ir_builders.py: Reusable IR construction patterns

Import directly from those modules instead of this one.
"""

import llvmlite.ir as ir


# ==============================================================================
# Module Creation Utilities
# ==============================================================================

def create_stdlib_module(name: str) -> ir.Module:
    """Create a new LLVM module for stdlib with standard naming and settings.

    Args:
        name: Module name (e.g., "core.primitives", "collections.strings")

    Returns:
        A new LLVM IR module with default target triple.

    Example:
        >>> module = create_stdlib_module("time")
        >>> # Now generate functions into the module
    """
    module = ir.Module(name=f"stdlib.{name}")
    module.triple = ""  # Use default target triple
    return module
