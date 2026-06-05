"""
Core runtime coordinator for LLVM code generation.

This module provides the main LLVMRuntime class that coordinates all runtime
operations by delegating to specialized sub-modules.
"""
from __future__ import annotations

import typing

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType

from sushi_lang.backend.runtime.externs.libc_strings import LibCStrings
from sushi_lang.backend.runtime.externs.libc_ctype import LibCCType
from sushi_lang.backend.runtime.externs.libc_process import LibCProcess
from sushi_lang.backend.runtime.externs.libc_stdio import LibCStdio
from sushi_lang.backend.runtime.strings import StringOperations
from sushi_lang.backend.runtime.formatting import FormattingOperations
from sushi_lang.backend.runtime.errors import RuntimeErrors

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# Reserved built-in extern symbols and their canonical C-ABI signatures, expressed
# in the FFI type allowlist (BuiltinType / ForeignPtrType). An external whose
# link-name matches one of these but whose signature differs triggers CE5001.
# An identical signature is allowed (LLVM deduplicates declarations).
#
# Each entry maps a C link-name to (param_types_tuple, return_type). This manifest
# is colocated with declare_externs (below) and kept in sync by a test that asserts
# every reserved name is actually declared.
RESERVED_EXTERNS: dict[str, tuple] = {
    "strlen":  ((BuiltinType.STRING,), BuiltinType.I64),
    "strcmp":  ((ForeignPtrType(), ForeignPtrType()), BuiltinType.I32),
    "memcmp":  ((ForeignPtrType(), ForeignPtrType(), BuiltinType.I32), BuiltinType.I32),
    "sprintf": ((ForeignPtrType(), ForeignPtrType()), BuiltinType.I32),
    "printf":  ((ForeignPtrType(),), BuiltinType.I32),
    "malloc":  ((BuiltinType.I64,), ForeignPtrType()),
    "free":    ((ForeignPtrType(),), BuiltinType.BLANK),
    "exit":    ((BuiltinType.I32,), BuiltinType.BLANK),
}


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
