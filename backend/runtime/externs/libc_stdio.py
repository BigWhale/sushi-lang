"""
External declarations for C standard library I/O functions.

This module provides declarations for file and stream I/O functions from <stdio.h>:
- printf, fprintf: Formatted output
- fopen, fclose: File open/close
- fgets, fputs, fgetc, fputc: Text I/O
- fread, fwrite: Binary I/O
- fseek, ftell, rewind: File positioning
- feof, ferror: Status checking
- stdin, stdout, stderr: Standard stream handles (platform-specific names)
"""
from __future__ import annotations

import typing

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.platform_detect import get_current_platform

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class LibCStdio:
    """Manages external declarations for C stdio functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

        # Function references - declared immediately for type safety
        self.printf: ir.Function
        self.fprintf: ir.Function
        self.fopen: ir.Function
        self.fclose: ir.Function
        self.fgets: ir.Function
        self.fgetc: ir.Function
        self.fputc: ir.Function
        self.fread: ir.Function
        self.fwrite: ir.Function
        self.fseek: ir.Function
        self.ftell: ir.Function
        self.rewind: ir.Function
        self.feof: ir.Function
        self.ferror: ir.Function

        # Global variable references (FILE* handles) - declared immediately for type safety
        self.stdin_handle: ir.GlobalVariable
        self.stdout_handle: ir.GlobalVariable
        self.stderr_handle: ir.GlobalVariable

    def declare_all(self) -> None:
        """Declare all stdio functions and globals."""
        self._declare_printf()
        self._declare_fprintf()
        self._declare_fopen()
        self._declare_fclose()
        self._declare_fgets()
        self._declare_fgetc()
        self._declare_fputc()
        self._declare_fread()
        self._declare_fwrite()
        self._declare_fseek()
        self._declare_ftell()
        self._declare_rewind()
        self._declare_feof()
        self._declare_ferror()
        self._declare_stdio_handles()

    def _declare_printf(self) -> None:
        """Declare printf: int printf(const char* format, ...)"""
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [self.codegen.i8.as_pointer()],
            var_arg=True
        )
        existing = self.codegen.module.globals.get("printf")
        if isinstance(existing, ir.Function):
            self.printf = existing
        else:
            self.printf = ir.Function(self.codegen.module, fn_ty, name="printf")

    def _declare_fprintf(self) -> None:
        """Declare fprintf: int fprintf(FILE* stream, const char* format, ...)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [file_ptr_ty, self.codegen.i8.as_pointer()],
            var_arg=True
        )
        existing = self.codegen.module.globals.get("fprintf")
        if isinstance(existing, ir.Function):
            self.fprintf = existing
        else:
            self.fprintf = ir.Function(self.codegen.module, fn_ty, name="fprintf")

    def _declare_fopen(self) -> None:
        """Declare fopen: FILE* fopen(const char* filename, const char* mode)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            file_ptr_ty,
            [self.codegen.i8.as_pointer(), self.codegen.i8.as_pointer()]
        )
        existing = self.codegen.module.globals.get("fopen")
        if isinstance(existing, ir.Function):
            self.fopen = existing
        else:
            self.fopen = ir.Function(self.codegen.module, fn_ty, name="fopen")

    def _declare_fclose(self) -> None:
        """Declare fclose: int fclose(FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("fclose")
        if isinstance(existing, ir.Function):
            self.fclose = existing
        else:
            self.fclose = ir.Function(self.codegen.module, fn_ty, name="fclose")

    def _declare_fgets(self) -> None:
        """Declare fgets: char* fgets(char* str, int size, FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i8.as_pointer(),
            [self.codegen.i8.as_pointer(), self.codegen.i32, file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("fgets")
        if isinstance(existing, ir.Function):
            self.fgets = existing
        else:
            self.fgets = ir.Function(self.codegen.module, fn_ty, name="fgets")

    def _declare_fgetc(self) -> None:
        """Declare fgetc: int fgetc(FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("fgetc")
        if isinstance(existing, ir.Function):
            self.fgetc = existing
        else:
            self.fgetc = ir.Function(self.codegen.module, fn_ty, name="fgetc")

    def _declare_fputc(self) -> None:
        """Declare fputc: int fputc(int c, FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [self.codegen.i32, file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("fputc")
        if isinstance(existing, ir.Function):
            self.fputc = existing
        else:
            self.fputc = ir.Function(self.codegen.module, fn_ty, name="fputc")

    def _declare_fread(self) -> None:
        """Declare fread: size_t fread(void* ptr, size_t size, size_t nmemb, FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        size_t_ty = ir.IntType(INT64_BIT_WIDTH)
        fn_ty = ir.FunctionType(
            size_t_ty,
            [self.codegen.i8.as_pointer(), size_t_ty, size_t_ty, file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("fread")
        if isinstance(existing, ir.Function):
            self.fread = existing
        else:
            self.fread = ir.Function(self.codegen.module, fn_ty, name="fread")

    def _declare_fwrite(self) -> None:
        """Declare fwrite: size_t fwrite(const void* ptr, size_t size, size_t nmemb, FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        size_t_ty = ir.IntType(INT64_BIT_WIDTH)
        fn_ty = ir.FunctionType(
            size_t_ty,
            [self.codegen.i8.as_pointer(), size_t_ty, size_t_ty, file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("fwrite")
        if isinstance(existing, ir.Function):
            self.fwrite = existing
        else:
            self.fwrite = ir.Function(self.codegen.module, fn_ty, name="fwrite")

    def _declare_fseek(self) -> None:
        """Declare fseek: int fseek(FILE* stream, long offset, int whence)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [file_ptr_ty, ir.IntType(INT64_BIT_WIDTH), self.codegen.i32]
        )
        existing = self.codegen.module.globals.get("fseek")
        if isinstance(existing, ir.Function):
            self.fseek = existing
        else:
            self.fseek = ir.Function(self.codegen.module, fn_ty, name="fseek")

    def _declare_ftell(self) -> None:
        """Declare ftell: long ftell(FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            ir.IntType(INT64_BIT_WIDTH),
            [file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("ftell")
        if isinstance(existing, ir.Function):
            self.ftell = existing
        else:
            self.ftell = ir.Function(self.codegen.module, fn_ty, name="ftell")

    def _declare_rewind(self) -> None:
        """Declare rewind: void rewind(FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            ir.VoidType(),
            [file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("rewind")
        if isinstance(existing, ir.Function):
            self.rewind = existing
        else:
            self.rewind = ir.Function(self.codegen.module, fn_ty, name="rewind")

    def _declare_feof(self) -> None:
        """Declare feof: int feof(FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("feof")
        if isinstance(existing, ir.Function):
            self.feof = existing
        else:
            self.feof = ir.Function(self.codegen.module, fn_ty, name="feof")

    def _declare_ferror(self) -> None:
        """Declare ferror: int ferror(FILE* stream)"""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [file_ptr_ty]
        )
        existing = self.codegen.module.globals.get("ferror")
        if isinstance(existing, ir.Function):
            self.ferror = existing
        else:
            self.ferror = ir.Function(self.codegen.module, fn_ty, name="ferror")

    def _declare_stdio_handles(self) -> None:
        """Declare global variables for stdin, stdout, stderr FILE* handles.

        These are external globals provided by the C standard library.
        Names are platform-specific:
        - macOS/Darwin: __stdinp, __stdoutp, __stderrp
        - Linux: stdin, stdout, stderr
        """
        # Detect platform and get correct handle names
        platform = get_current_platform()

        if platform.is_darwin:
            stdin_name = "__stdinp"
            stdout_name = "__stdoutp"
            stderr_name = "__stderrp"
        elif platform.is_linux:
            stdin_name = "stdin"
            stdout_name = "stdout"
            stderr_name = "stderr"
        else:
            # Default to POSIX names (Linux-style) for other Unix-like systems
            stdin_name = "stdin"
            stdout_name = "stdout"
            stderr_name = "stderr"

        file_ptr_ty = self.codegen.i8.as_pointer()

        self.stdin_handle = ir.GlobalVariable(
            self.codegen.module, file_ptr_ty, name=stdin_name
        )
        self.stdin_handle.linkage = "external"

        self.stdout_handle = ir.GlobalVariable(
            self.codegen.module, file_ptr_ty, name=stdout_name
        )
        self.stdout_handle.linkage = "external"

        self.stderr_handle = ir.GlobalVariable(
            self.codegen.module, file_ptr_ty, name=stderr_name
        )
        self.stderr_handle.linkage = "external"
