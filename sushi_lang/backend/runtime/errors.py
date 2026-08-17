"""Runtime error handling for LLVM code generation."""
from __future__ import annotations

import hashlib
import typing

from llvmlite import ir

from sushi_lang.backend.constants import INT8_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I32
from sushi_lang.backend.runtime.constants import (
    ERRNO_TO_FILE_ERROR,
    ERRNO_DEFAULT_FILE_ERROR,
)
from sushi_lang.internals.errors import message_for, raise_internal_error

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def _message_global_name(error_code: str, message: str, kind: str = "msg") -> str:
    """Name a runtime-message global by its CONTENT, not just its code."""
    digest = hashlib.sha1(message.encode("utf-8")).hexdigest()[:8]
    return f".runtime_err_{kind}_{error_code}_{digest}"


class RuntimeErrors:
    """Manages runtime error emission and errno handling."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

    def _get_pct_s_format_ptr(self, builder: ir.IRBuilder) -> ir.Value:
        """Get an i8* to a shared, reusable "%s" format string constant."""
        fmt_name = ".runtime_err_pct_s"
        existing = self.codegen.module.globals.get(fmt_name)
        if existing and isinstance(existing, ir.GlobalVariable):
            fmt_const = existing
        else:
            data = bytearray(b"%s\x00")
            arr_ty = ir.ArrayType(ir.IntType(INT8_BIT_WIDTH), len(data))
            fmt_const = ir.GlobalVariable(self.codegen.module, arr_ty, name=fmt_name)
            fmt_const.linkage = 'private'
            fmt_const.global_constant = True
            fmt_const.initializer = ir.Constant(arr_ty, data)
        return builder.gep(fmt_const, [ZERO_I32, ZERO_I32], name="pct_s_ptr")

    def emit_runtime_error(self, error_code: str, **params) -> None:
        """Emit runtime error message to stderr and exit program."""
        builder = self.codegen.builder

        full_message = f"Runtime Error {error_code}: {message_for(error_code, **params)}\n"

        arr_ty = ir.ArrayType(ir.IntType(INT8_BIT_WIDTH), len(full_message) + 1)
        msg_name = _message_global_name(error_code, full_message)

        existing = self.codegen.module.globals.get(msg_name)
        if existing and isinstance(existing, ir.GlobalVariable):
            msg_const = existing
        else:
            msg_const = ir.GlobalVariable(self.codegen.module, arr_ty, name=msg_name)
            msg_const.linkage = 'private'
            msg_const.global_constant = True
            msg_const.initializer = ir.Constant(
                arr_ty,
                bytearray(full_message.encode('utf-8')) + bytearray([0])
            )

        msg_ptr = builder.gep(
            msg_const,
            [ZERO_I32, ZERO_I32],
            name="err_msg_ptr"
        )

        stderr_ptr = builder.load(self.codegen.runtime.libc_stdio.stderr_handle, name="stderr")

        pct_s_ptr = self._get_pct_s_format_ptr(builder)
        builder.call(self.codegen.runtime.libc_stdio.fprintf, [stderr_ptr, pct_s_ptr, msg_ptr])

        builder.call(self.codegen.runtime.libc_process.exit, [ir.Constant(self.codegen.i32, 1)])

    def emit_runtime_error_with_values(
        self, error_code: str, *values: ir.Value
    ) -> None:
        """Emit runtime error message with formatted values to stderr and exit program."""
        builder = self.codegen.builder

        # Create format string: "Runtime Error RE2020: <registry text>\n"
        full_format = f"Runtime Error {error_code}: {message_for(error_code)}\n"

        arr_ty = ir.ArrayType(ir.IntType(INT8_BIT_WIDTH), len(full_format) + 1)
        fmt_name = _message_global_name(error_code, full_format, kind="fmt")

        existing = self.codegen.module.globals.get(fmt_name)
        if existing and isinstance(existing, ir.GlobalVariable):
            fmt_const = existing
        else:
            fmt_const = ir.GlobalVariable(self.codegen.module, arr_ty, name=fmt_name)
            fmt_const.linkage = 'private'
            fmt_const.global_constant = True
            fmt_const.initializer = ir.Constant(
                arr_ty,
                bytearray(full_format.encode('utf-8')) + bytearray([0])
            )

        fmt_ptr = builder.gep(
            fmt_const,
            [ZERO_I32, ZERO_I32],
            name="err_fmt_ptr"
        )

        stderr_ptr = builder.load(self.codegen.runtime.libc_stdio.stderr_handle, name="stderr")

        builder.call(self.codegen.runtime.libc_stdio.fprintf, [stderr_ptr, fmt_ptr] + list(values))

        builder.call(self.codegen.runtime.libc_process.exit, [ir.Constant(self.codegen.i32, 1)])

    def get_errno(self) -> ir.Value:
        """Get the current errno value."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        assert (
            self.codegen.runtime.libc_process.errno_location is not None
        ), "errno_location function not declared"

        errno_ptr = self.codegen.builder.call(self.codegen.runtime.libc_process.errno_location, [])

        return self.codegen.builder.load(errno_ptr, name="errno_value")

    def map_errno_to_file_error(self, errno_value: ir.Value) -> ir.Value:
        """Map errno value to FileError enum variant tag."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        builder = self.codegen.builder

        result = ir.Constant(self.codegen.i32, ERRNO_DEFAULT_FILE_ERROR)

        for errno_val, file_error_tag in reversed(list(ERRNO_TO_FILE_ERROR.items())):
            errno_const = ir.Constant(self.codegen.i32, errno_val)
            file_error_const = ir.Constant(self.codegen.i32, file_error_tag)

            is_match = builder.icmp_signed('==', errno_value, errno_const)
            result = builder.select(is_match, file_error_const, result)

        return result
