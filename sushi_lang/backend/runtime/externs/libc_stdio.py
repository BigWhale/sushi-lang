"""External declarations for C standard library I/O functions."""
from __future__ import annotations

import typing

from llvmlite import ir
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

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
        self.fd_write = declare_libc(self.codegen.module, "write")

    def _declare_setvbuf(self) -> None:
        """Declare setvbuf: int setvbuf(FILE* stream, char* buf, int mode, size_t size)."""
        self.setvbuf = declare_libc(self.codegen.module, "setvbuf")

    def _declare_fprintf(self) -> None:
        """Declare fprintf: int fprintf(FILE* stream, const char* format, ...)"""
        self.fprintf = declare_libc(self.codegen.module, "fprintf")

    def _declare_fwrite(self) -> None:
        """Declare fwrite: size_t fwrite(const void* ptr, size_t size, size_t nmemb, FILE* stream)"""
        self.fwrite = declare_libc(self.codegen.module, "fwrite")

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
