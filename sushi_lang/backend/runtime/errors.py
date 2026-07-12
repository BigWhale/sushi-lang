"""
Runtime error handling for LLVM code generation.

This module provides functions for emitting runtime error messages and
mapping system errors (errno) to Sushi FileError enum variants.
"""
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
    """Name a runtime-message global by its CONTENT, not just its code.

    Naming it by the code alone interned ONE global per code, so two sites sharing
    a code silently shared whichever text was emitted first -- RE2021 had three
    different messages and printed one of them. Hashing the message means identical
    texts still dedupe and different texts get distinct globals.
    """
    digest = hashlib.sha1(message.encode("utf-8")).hexdigest()[:8]
    return f".runtime_err_{kind}_{error_code}_{digest}"


class RuntimeErrors:
    """Manages runtime error emission and errno handling."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
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
        """Emit runtime error message to stderr and exit program.

        Generates LLVM IR to:
        1. Print error message to stderr using fprintf
        2. Terminate program with exit(1)

        Args:
            error_code: Runtime error code (e.g., "RE2021")
            **params: Format parameters for the registry text, if it has any

        Note:
            This function does NOT return - it emits an exit call.
            The current basic block will be terminated.
        """
        builder = self.codegen.builder

        # The registry owns the text. A code says the same thing wherever it fires.
        full_message = f"Runtime Error {error_code}: {message_for(error_code, **params)}\n"

        # Create global string constant for error message (or reuse if exists)
        arr_ty = ir.ArrayType(ir.IntType(INT8_BIT_WIDTH), len(full_message) + 1)
        msg_name = _message_global_name(error_code, full_message)

        # Check if global already exists
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

        # Get pointer to the string
        msg_ptr = builder.gep(
            msg_const,
            [ZERO_I32, ZERO_I32],
            name="err_msg_ptr"
        )

        # Load stderr handle
        stderr_ptr = builder.load(self.codegen.runtime.libc_stdio.stderr_handle, name="stderr")

        # Call fprintf(stderr, "%s", message) - passing the message through a "%s"
        # format so any '%' inside it is not interpreted as a conversion specifier.
        pct_s_ptr = self._get_pct_s_format_ptr(builder)
        builder.call(self.codegen.runtime.libc_stdio.fprintf, [stderr_ptr, pct_s_ptr, msg_ptr])

        # Call exit(1) to terminate program
        builder.call(self.codegen.runtime.libc_process.exit, [ir.Constant(self.codegen.i32, 1)])

    def emit_runtime_error_with_values(
        self, error_code: str, *values: ir.Value
    ) -> None:
        """Emit runtime error message with formatted values to stderr and exit program.

        Similar to emit_runtime_error, but the registry text IS the printf format:
        its %d / %s conversions must match the values passed here.

        Generates LLVM IR to:
        1. Print formatted error message to stderr using fprintf
        2. Terminate program with exit(1)

        Args:
            error_code: Runtime error code (e.g., "RE2020")
            *values: LLVM values to interpolate into the registry's format string

        Example:
            RE2020's text is "array index %d out of bounds for array of size %d", so:
            emit_runtime_error_with_values("RE2020", index, size)

        Note:
            This function does NOT return - it emits an exit call.
            The current basic block will be terminated.
        """
        builder = self.codegen.builder

        # Create format string: "Runtime Error RE2020: <registry text>\n"
        full_format = f"Runtime Error {error_code}: {message_for(error_code)}\n"

        # Create global string constant for format string
        arr_ty = ir.ArrayType(ir.IntType(INT8_BIT_WIDTH), len(full_format) + 1)
        fmt_name = _message_global_name(error_code, full_format, kind="fmt")

        # Check if global already exists
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

        # Get pointer to the format string
        fmt_ptr = builder.gep(
            fmt_const,
            [ZERO_I32, ZERO_I32],
            name="err_fmt_ptr"
        )

        # Load stderr handle
        stderr_ptr = builder.load(self.codegen.runtime.libc_stdio.stderr_handle, name="stderr")

        # Call fprintf(stderr, format, values...)
        builder.call(self.codegen.runtime.libc_stdio.fprintf, [stderr_ptr, fmt_ptr] + list(values))

        # Call exit(1) to terminate program
        builder.call(self.codegen.runtime.libc_process.exit, [ir.Constant(self.codegen.i32, 1)])

    def get_errno(self) -> ir.Value:
        """Get the current errno value.

        Returns the value of the thread-local errno variable by calling
        __error() (macOS) or __errno_location() (Linux).

        Returns:
            The current errno value as i32.

        Raises:
            AssertionError: If errno_location function is not declared.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        assert (
            self.codegen.runtime.libc_process.errno_location is not None
        ), "errno_location function not declared"

        # Call __error() or __errno_location() to get pointer to errno
        errno_ptr = self.codegen.builder.call(self.codegen.runtime.libc_process.errno_location, [])

        # Load the errno value
        return self.codegen.builder.load(errno_ptr, name="errno_value")

    def map_errno_to_file_error(self, errno_value: ir.Value) -> ir.Value:
        """Map errno value to FileError enum variant tag.

        This function generates LLVM IR that maps errno constants to
        FileError enum variant indices using a dictionary-based approach
        with a chain of select instructions.

        FileError enum variant mapping (must match CollectorPass._register_predefined_enums):
        - 0: NotFound (ENOENT)
        - 1: PermissionDenied (EPERM, EACCES)
        - 2: AlreadyExists (EEXIST)
        - 3: IsDirectory (EISDIR)
        - 4: DiskFull (ENOSPC)
        - 5: TooManyOpen (EMFILE)
        - 6: InvalidPath (ENAMETOOLONG)
        - 7: IOError (EIO)
        - 8: Other (default)

        Args:
            errno_value: The errno value to map (as i32).

        Returns:
            The FileError variant tag (as i32).
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        builder = self.codegen.builder

        # Start with default (Other)
        result = ir.Constant(self.codegen.i32, ERRNO_DEFAULT_FILE_ERROR)

        # Build chain of select instructions using the mapping dictionary
        # Process in reverse order so most common errors are checked last (more efficient)
        for errno_val, file_error_tag in reversed(list(ERRNO_TO_FILE_ERROR.items())):
            errno_const = ir.Constant(self.codegen.i32, errno_val)
            file_error_const = ir.Constant(self.codegen.i32, file_error_tag)

            # Check if errno matches this value
            is_match = builder.icmp_signed('==', errno_value, errno_const)
            result = builder.select(is_match, file_error_const, result)

        return result
