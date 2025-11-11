"""
External declarations for C standard library string functions.

This module provides declarations for string manipulation functions:
- strcmp: Pure LLVM intrinsic (llvm_strcmp)
- strlen: Pure LLVM intrinsic (llvm_strlen)
- sprintf: External libc function
- memcmp: External libc function
"""
from __future__ import annotations

import typing

from llvmlite import ir

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class LibCStrings:
    """Manages external declarations for C string functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

        # Function references - declared immediately for type safety
        self.strcmp: ir.Function
        self.strlen: ir.Function
        self.sprintf: ir.Function
        self.memcmp: ir.Function

    def declare_all(self) -> None:
        """Declare all string functions."""
        self._declare_strcmp()
        self._declare_strlen()
        self._declare_sprintf()
        self._declare_memcmp()

    def _declare_extern(
        self,
        attr_name: str,
        func_name: str,
        return_type: ir.Type,
        arg_types: list[ir.Type],
        var_arg: bool = False
    ) -> ir.Function:
        """Helper to declare external C function with reduced duplication.

        Args:
            attr_name: Name of attribute to store function (e.g., "strcmp")
            func_name: Name of C function (usually same as attr_name)
            return_type: LLVM return type
            arg_types: List of LLVM argument types
            var_arg: Whether function accepts variable arguments

        Returns:
            The declared or existing function (guaranteed non-None).
        """
        # Check if function already exists in module
        existing_global = self.codegen.module.globals.get(func_name)
        if isinstance(existing_global, ir.Function):
            setattr(self, attr_name, existing_global)
            return existing_global

        # Declare new function
        fn_ty = ir.FunctionType(return_type, arg_types, var_arg=var_arg)
        func = ir.Function(self.codegen.module, fn_ty, name=func_name)
        setattr(self, attr_name, func)
        return func

    def _declare_strcmp(self) -> None:
        """Declare strcmp: int strcmp(const char* s1, const char* s2)"""
        self.strcmp = self._declare_extern(
            "strcmp",
            "strcmp",
            self.codegen.i32,
            [self.codegen.i8.as_pointer(), self.codegen.i8.as_pointer()]
        )

    def _declare_strlen(self) -> None:
        """Declare strlen: i32 llvm_strlen(i8* s)

        Uses pure LLVM IR intrinsic instead of libc strlen.
        """
        from stdlib.src.collections.strings_inline import emit_strlen_intrinsic_inline
        self.strlen = emit_strlen_intrinsic_inline(self.codegen.module)

    def _declare_sprintf(self) -> None:
        """Declare sprintf: int sprintf(char* str, const char* format, ...)"""
        self.sprintf = self._declare_extern(
            "sprintf",
            "sprintf",
            self.codegen.i32,
            [self.codegen.i8.as_pointer(), self.codegen.i8.as_pointer()],
            var_arg=True
        )

    def _declare_memcmp(self) -> None:
        """Declare memcmp: int memcmp(const void* s1, const void* s2, size_t n)"""
        self.memcmp = self._declare_extern(
            "memcmp",
            "memcmp",
            self.codegen.i32,
            [self.codegen.i8.as_pointer(), self.codegen.i8.as_pointer(), self.codegen.i32]
        )
