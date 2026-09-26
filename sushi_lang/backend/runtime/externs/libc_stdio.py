"""External declarations for C standard library I/O functions."""
from __future__ import annotations

import typing

from llvmlite import ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.backend.platform_detect import get_current_platform

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class LibCStdio:
    """Manages external declarations for C stdio functions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

        self.fprintf: ir.Function
        self.fwrite: ir.Function
        # POSIX write(2), not stdio. It is the ONE route a print statement takes to a
        # descriptor (HANDLES.md, Phase 5). The name says `fd_` so that a reader never
        # mistakes it for `fwrite`, which buffers and which the console no longer uses.
        self.fd_write: ir.Function
        self.setvbuf: ir.Function

        self.stdin_handle: ir.GlobalVariable
        self.stdout_handle: ir.GlobalVariable
        self.stderr_handle: ir.GlobalVariable

    def declare_all(self) -> None:
        """Declare all stdio functions and globals."""
        self._declare_fprintf()
        self._declare_fwrite()
        self._declare_fd_write()
        self._declare_setvbuf()
        self._declare_stdio_handles()

    def _declare_fd_write(self) -> None:
        """Declare write: ssize_t write(int fd, const void* buf, size_t count)."""
        fn_ty = ir.FunctionType(
            self.codegen.types.i64,
            [self.codegen.i32, self.codegen.i8.as_pointer(), self.codegen.types.i64]
        )
        existing = self.codegen.module.globals.get("write")
        if isinstance(existing, ir.Function):
            self.fd_write = existing
        else:
            self.fd_write = ir.Function(self.codegen.module, fn_ty, name="write")

    def _declare_setvbuf(self) -> None:
        """Declare setvbuf: int setvbuf(FILE* stream, char* buf, int mode, size_t size)."""
        file_ptr_ty = self.codegen.i8.as_pointer()
        fn_ty = ir.FunctionType(
            self.codegen.i32,
            [file_ptr_ty, self.codegen.i8.as_pointer(), self.codegen.i32,
             self.codegen.types.i64]
        )
        existing = self.codegen.module.globals.get("setvbuf")
        if isinstance(existing, ir.Function):
            self.setvbuf = existing
        else:
            self.setvbuf = ir.Function(self.codegen.module, fn_ty, name="setvbuf")

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

    def _declare_stdio_handles(self) -> None:
        """Declare global variables for stdin, stdout, stderr FILE* handles."""
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
