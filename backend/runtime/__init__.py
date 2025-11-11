"""
Runtime support module for LLVM code generation.

This module provides runtime support including external function declarations,
string operations, formatting, error handling, and system integration.

The module is organized into specialized sub-modules:
- externs: External C library function declarations
- strings: String operations and UTF-8 support
- formatting: Format strings and print operations
- errors: Runtime error handling
- constants: System constants and mappings
"""
from __future__ import annotations

from backend.runtime.core import LLVMRuntime

__all__ = ["LLVMRuntime"]
