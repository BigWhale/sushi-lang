"""External declarations for C standard library process control functions."""
from __future__ import annotations

import typing

from llvmlite import ir

from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class LibCProcess:
    """Manages external declarations for C process control functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

        self.exit: ir.Function

    def declare_all(self) -> None:
        """Declare all process control functions."""
        self._declare_exit()

    def _declare_exit(self) -> None:
        """Declare exit: void exit(int status)"""
        self.exit = declare_libc(self.codegen.module, "exit")
