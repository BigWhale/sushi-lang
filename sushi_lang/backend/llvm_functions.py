"""
LLVM function management for the Sushi language compiler.

This module re-exports the modular function management system from
backend/functions/. The implementation has been refactored into specialized
components for better separation of concerns:

- helpers.py: Parameter validation, scope management, default returns
- declarations.py: Function prototype generation
- definitions.py: Function body emission
- main_wrapper.py: Main function C compatibility wrapper
- __init__.py: Unified facade pattern

For backward compatibility, this module maintains the original interface
while delegating to the new modular implementation.
"""
from sushi_lang.backend.functions import LLVMFunctionManager, declare_stdlib_function

__all__ = ['LLVMFunctionManager', 'declare_stdlib_function']
