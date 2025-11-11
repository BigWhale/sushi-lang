"""
Runtime error handling for LLVM code generation.

This module provides functions for emitting runtime error messages and
mapping system errors (errno) to Sushi FileError enum variants.
"""
from __future__ import annotations

import typing

from llvmlite import ir

from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.llvm_constants import ZERO_I32
from backend.runtime.constants import (
    ERRNO_TO_FILE_ERROR,
    ERRNO_DEFAULT_FILE_ERROR,
)
from internals.errors import raise_internal_error

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class RuntimeErrors:
    """Manages runtime error emission and errno handling."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

    def emit_runtime_error(self, error_code: str, message: str) -> None:
        """Emit runtime error message to stderr and exit program.

        Generates LLVM IR to:
        1. Print error message to stderr using fprintf
        2. Terminate program with exit(1)

        Args:
            error_code: Runtime error code (e.g., "RE2021")
            message: Human-readable error message

        Note:
            This function does NOT return - it emits an exit call.
            The current basic block will be terminated.
        """
        builder = self.codegen.builder

        # Format the error message: "Runtime Error RE2021: message\n"
        full_message = f"Runtime Error {error_code}: {message}\\n"

        # Create global string constant for error message (or reuse if exists)
        arr_ty = ir.ArrayType(ir.IntType(INT8_BIT_WIDTH), len(full_message) + 1)
        msg_name = f".runtime_err_{error_code}"

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

        # Call fprintf(stderr, message)
        builder.call(self.codegen.runtime.libc_stdio.fprintf, [stderr_ptr, msg_ptr])

        # Call exit(1) to terminate program
        builder.call(self.codegen.runtime.libc_process.exit, [ir.Constant(self.codegen.i32, 1)])

    def emit_runtime_error_with_values(
        self, error_code: str, format_string: str, *values: ir.Value
    ) -> None:
        """Emit runtime error message with formatted values to stderr and exit program.

        Similar to emit_runtime_error but allows including runtime values in the error message.

        Generates LLVM IR to:
        1. Print formatted error message to stderr using fprintf
        2. Terminate program with exit(1)

        Args:
            error_code: Runtime error code (e.g., "RE2020")
            format_string: Printf-style format string with %d, %s, etc.
            *values: LLVM values to interpolate into the format string

        Example:
            emit_runtime_error_with_values(
                "RE2020", "index %d out of bounds for array of size %d", index, size
            )

        Note:
            This function does NOT return - it emits an exit call.
            The current basic block will be terminated.
        """
        builder = self.codegen.builder

        # Create format string: "Runtime Error RE2020: <format_string>\n"
        full_format = f"Runtime Error {error_code}: {format_string}\\n"

        # Create global string constant for format string
        arr_ty = ir.ArrayType(ir.IntType(INT8_BIT_WIDTH), len(full_format) + 1)
        fmt_name = f".runtime_err_fmt_{error_code}"

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
