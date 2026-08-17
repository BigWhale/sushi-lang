"""Core runtime coordinator for LLVM code generation."""
from __future__ import annotations

import typing


from sushi_lang.backend.runtime.externs.libc_strings import LibCStrings
from sushi_lang.backend.runtime.externs.libc_ctype import LibCCType
from sushi_lang.backend.runtime.externs.libc_process import LibCProcess
from sushi_lang.backend.runtime.externs.libc_stdio import LibCStdio
from sushi_lang.backend.runtime.strings import StringOperations
from sushi_lang.backend.runtime.formatting import FormattingOperations
from sushi_lang.backend.runtime.errors import RuntimeErrors

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class LLVMRuntime:
    """Main runtime coordinator that manages all runtime support operations."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize runtime support with reference to main codegen instance."""
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
        """Declare all external runtime functions and global constants."""
        # Declare external C library functions
        self.libc_strings.declare_all()
        self.libc_ctype.declare_all()
        self.libc_process.declare_all()
        self.libc_stdio.declare_all()

        # Declare UTF-8 support functions
        self.strings.declare_utf8_functions()

        # Declare format strings
        self.formatting.declare_format_strings()
