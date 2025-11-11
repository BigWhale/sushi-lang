"""
External declarations for C standard library process control functions.

This module provides declarations for process control and system functions:
- exit: Program termination
- __error/__errno_location: Thread-local errno access (platform-specific)
"""
from __future__ import annotations

import typing

from llvmlite import ir

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class LibCProcess:
    """Manages external declarations for C process control functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

        # Function references - declared immediately for type safety
        self.exit: ir.Function
        self.errno_location: ir.Function

    def declare_all(self) -> None:
        """Declare all process control functions."""
        self._declare_exit()
        self._declare_errno_location()

    def _declare_exit(self) -> None:
        """Declare exit: void exit(int status)

        Terminates the program with the given exit status.
        """
        fn_ty = ir.FunctionType(
            ir.VoidType(),  # void return
            [self.codegen.i32]  # int status
        )
        existing = self.codegen.module.globals.get("exit")
        if isinstance(existing, ir.Function):
            self.exit = existing
        else:
            self.exit = ir.Function(self.codegen.module, fn_ty, name="exit")

    def _declare_errno_location(self) -> None:
        """Declare errno access function: int* __error() or int* __errno_location()

        Platform-specific function to get thread-local errno:
        - macOS: __error()
        - Linux: __errno_location()

        Returns a pointer to the thread-local errno variable.
        """
        fn_ty = ir.FunctionType(
            self.codegen.i32.as_pointer(),  # Returns int* (pointer to errno)
            []  # No parameters
        )

        # Platform-specific errno location function
        # Check if already declared
        existing = self.codegen.module.globals.get("__error")
        if isinstance(existing, ir.Function):
            self.errno_location = existing
            return

        existing = self.codegen.module.globals.get("__errno_location")
        if isinstance(existing, ir.Function):
            self.errno_location = existing
            return

        # Declare based on platform
        from backend.platform_detect import get_current_platform
        platform = get_current_platform()

        if platform.is_linux:
            function_name = "__errno_location"
        else:  # macOS, BSD, etc.
            function_name = "__error"

        self.errno_location = ir.Function(self.codegen.module, fn_ty, name=function_name)
