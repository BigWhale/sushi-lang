"""External declarations for C standard library string functions."""
from __future__ import annotations

import typing

from llvmlite import ir

from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class LibCStrings:
    """Manages external declarations for C string functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

        self.strlen: ir.Function
        self.sprintf: ir.Function
        self.memcmp: ir.Function

    def declare_all(self) -> None:
        """Declare all string functions."""
        self._declare_strlen()
        self._declare_sprintf()
        self._declare_memcmp()

    def _declare_strlen(self) -> None:
        """Declare strlen: i32 llvm_strlen(i8* s)"""
        from sushi_lang.sushi_stdlib.src.collections.strings_inline import emit_strlen_intrinsic_inline
        self.strlen = emit_strlen_intrinsic_inline(self.codegen.module)

    def _declare_sprintf(self) -> None:
        """Declare sprintf: int sprintf(char* str, const char* format, ...)"""
        self.sprintf = declare_libc(self.codegen.module, "sprintf")

    def _declare_memcmp(self) -> None:
        """Declare memcmp: int memcmp(const void* s1, const void* s2, size_t n)"""
        self.memcmp = declare_libc(self.codegen.module, "memcmp")
