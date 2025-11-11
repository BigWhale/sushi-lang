"""
Core runtime coordinator for LLVM code generation.

This module provides the main LLVMRuntime class that coordinates all runtime
operations by delegating to specialized sub-modules.
"""
from __future__ import annotations

import typing

from llvmlite import ir

from backend.runtime.externs.libc_strings import LibCStrings
from backend.runtime.externs.libc_ctype import LibCCType
from backend.runtime.externs.libc_process import LibCProcess
from backend.runtime.externs.libc_stdio import LibCStdio
from backend.runtime.strings import StringOperations
from backend.runtime.formatting import FormattingOperations
from backend.runtime.errors import RuntimeErrors

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class LLVMRuntime:
    """Main runtime coordinator that manages all runtime support operations.

    This class delegates to specialized sub-modules for different categories
    of runtime operations, providing a unified interface while maintaining
    clean separation of concerns.
    """

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize runtime support with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

        # Initialize sub-modules
        self.libc_strings = LibCStrings(codegen)
        self.libc_ctype = LibCCType(codegen)
        self.libc_process = LibCProcess(codegen)
        self.libc_stdio = LibCStdio(codegen)
        self.strings = StringOperations(codegen)
        self.formatting = FormattingOperations(codegen)
        self.errors = RuntimeErrors(codegen)

    def declare_externs(self) -> None:
        """Declare all external runtime functions and global constants.

        Orchestrates the declaration of all external C library functions
        and global constants needed for runtime support.
        """
        # Declare external C library functions
        self.libc_strings.declare_all()
        self.libc_ctype.declare_all()
        self.libc_process.declare_all()
        self.libc_stdio.declare_all()

        # Declare UTF-8 support functions
        self.strings.declare_utf8_functions()

        # Declare format strings
        self.formatting.declare_format_strings()
