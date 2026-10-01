"""External declarations for C standard library character type functions."""
from __future__ import annotations

import typing

from llvmlite import ir

from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class LibCCType:
    """Manages external declarations for C character type functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

        self.toupper: ir.Function
        self.tolower: ir.Function
        self.isspace: ir.Function
        self.isdigit: ir.Function
        self.isalnum: ir.Function

    def declare_all(self) -> None:
        """Declare all character type functions."""
        self._declare_toupper()
        self._declare_tolower()
        self._declare_isspace()
        self._declare_isdigit()
        self._declare_isalnum()

    def _declare_toupper(self) -> None:
        """Declare toupper: int toupper(int c)"""
        self.toupper = declare_libc(self.codegen.module, "toupper")

    def _declare_tolower(self) -> None:
        """Declare tolower: int tolower(int c)"""
        self.tolower = declare_libc(self.codegen.module, "tolower")

    def _declare_isspace(self) -> None:
        """Declare isspace: int isspace(int c)"""
        self.isspace = declare_libc(self.codegen.module, "isspace")

    def _declare_isdigit(self) -> None:
        """Declare isdigit: int isdigit(int c)"""
        self.isdigit = declare_libc(self.codegen.module, "isdigit")

    def _declare_isalnum(self) -> None:
        """Declare isalnum: int isalnum(int c)"""
        self.isalnum = declare_libc(self.codegen.module, "isalnum")
