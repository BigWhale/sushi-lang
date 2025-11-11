"""
External declarations for C standard library character type functions.

This module provides declarations for character classification and conversion from <ctype.h>:
- toupper, tolower, isspace, isdigit, isalpha, isalnum
"""
from __future__ import annotations

import typing

from llvmlite import ir

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class LibCCType:
    """Manages external declarations for C character type functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

        # Function references - declared immediately for type safety
        self.toupper: ir.Function
        self.tolower: ir.Function
        self.isspace: ir.Function
        self.isdigit: ir.Function
        self.isalpha: ir.Function
        self.isalnum: ir.Function

    def declare_all(self) -> None:
        """Declare all character type functions."""
        self._declare_toupper()
        self._declare_tolower()
        self._declare_isspace()
        self._declare_isdigit()
        self._declare_isalpha()
        self._declare_isalnum()

    def _declare_ctype_func(self, name: str) -> ir.Function:
        """Helper to declare ctype function: int func(int c).

        All ctype.h functions have the same signature: int -> int.
        This eliminates significant duplication.

        Args:
            name: Function name (e.g., "toupper", "isspace")

        Returns:
            The declared or existing function (guaranteed non-None).
        """
        # Check if function already exists in module
        existing_global = self.codegen.module.globals.get(name)
        if isinstance(existing_global, ir.Function):
            setattr(self, name, existing_global)
            return existing_global

        # Declare new function: int func(int c)
        fn_ty = ir.FunctionType(self.codegen.i32, [self.codegen.i32])
        func = ir.Function(self.codegen.module, fn_ty, name=name)
        setattr(self, name, func)
        return func

    def _declare_toupper(self) -> None:
        """Declare toupper: int toupper(int c)"""
        self.toupper = self._declare_ctype_func("toupper")

    def _declare_tolower(self) -> None:
        """Declare tolower: int tolower(int c)"""
        self.tolower = self._declare_ctype_func("tolower")

    def _declare_isspace(self) -> None:
        """Declare isspace: int isspace(int c)"""
        self.isspace = self._declare_ctype_func("isspace")

    def _declare_isdigit(self) -> None:
        """Declare isdigit: int isdigit(int c)"""
        self.isdigit = self._declare_ctype_func("isdigit")

    def _declare_isalpha(self) -> None:
        """Declare isalpha: int isalpha(int c)"""
        self.isalpha = self._declare_ctype_func("isalpha")

    def _declare_isalnum(self) -> None:
        """Declare isalnum: int isalnum(int c)"""
        self.isalnum = self._declare_ctype_func("isalnum")
