"""
Formatting operations for LLVM code generation.

This module provides functions for type-to-string conversions, format string
management, and printf-style output operations.
"""
from __future__ import annotations

import typing

from llvmlite import ir

from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.llvm_constants import make_i64_const
from backend.runtime.constants import FORMAT_STRINGS
from internals.errors import raise_internal_error

if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class FormattingOperations:
    """Manages formatting operations and type conversions."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and module.
        """
        self.codegen = codegen

        # Global format string constants (cached)
        self.fmt_i32: ir.GlobalVariable | None = None
        self.fmt_i64: ir.GlobalVariable | None = None
        self.fmt_u32: ir.GlobalVariable | None = None
        self.fmt_u64: ir.GlobalVariable | None = None
        self.fmt_str: ir.GlobalVariable | None = None
        self.fmt_f32: ir.GlobalVariable | None = None
        self.fmt_f64: ir.GlobalVariable | None = None
        self.fmt_bool_true: ir.GlobalVariable | None = None
        self.fmt_bool_false: ir.GlobalVariable | None = None

    def declare_format_strings(self) -> None:
        """Declare global format string constants for printf operations.

        Creates read-only global constants for integer, float, and string formatting.
        Uses the FORMAT_STRINGS dictionary from constants module for configuration.
        """
        # Pre-create format strings (without GEP, just the globals)
        for name in ["i32", "str", "f32", "f64"]:
            attr_name = f"fmt_{name}"
            if getattr(self, attr_name, None) is None:
                global_str = self._create_format_string(name, FORMAT_STRINGS[name])
                setattr(self, attr_name, global_str)

    def emit_print_value(self, v: ir.Value, is_line: bool = False) -> None:
        """Generate printf call with appropriate format for value type.

        Determines the value type and calls printf with the corresponding
        format string. Supports integer, float, and string value printing.
        If is_line is True, appends a newline after the value.

        Args:
            v: The LLVM value to print.
            is_line: Whether to append a newline after printing (println behavior).

        Raises:
            AssertionError: If required runtime functions are not declared.
        """
        assert (
            self.codegen.builder is not None
            and self.codegen.runtime.libc_stdio.printf is not None
            and self.fmt_i32 is not None
            and self.fmt_str is not None
            and self.fmt_f32 is not None
            and self.fmt_f64 is not None
        )

        if self.codegen.types.is_string_type(v.type):
            fmt_ptr = self.codegen.utils.cstr_ptr(self.fmt_str)
            # Convert fat pointer to null-terminated C string for printf
            c_str = self.codegen.runtime.strings.emit_to_cstr(v)
            self.codegen.builder.call(self.codegen.runtime.libc_stdio.printf, [fmt_ptr, c_str])
        elif isinstance(v.type, ir.FloatType):
            fmt_ptr = self.codegen.utils.cstr_ptr(self.fmt_f32)
            # Convert f32 to f64 for printf (C variadic function requirement)
            f64_val = self.codegen.builder.fpext(v, self.codegen.types.f64)
            self.codegen.builder.call(self.codegen.runtime.libc_stdio.printf, [fmt_ptr, f64_val])
        elif isinstance(v.type, ir.DoubleType):
            fmt_ptr = self.codegen.utils.cstr_ptr(self.fmt_f64)
            self.codegen.builder.call(self.codegen.runtime.libc_stdio.printf, [fmt_ptr, v])
        else:
            fmt_ptr = self.codegen.utils.cstr_ptr(self.fmt_i32)
            self.codegen.builder.call(
                self.codegen.runtime.libc_stdio.printf, [fmt_ptr, self.codegen.utils.as_i32(v)]
            )

        # Print newline if this is println
        if is_line:
            newline_struct = self.codegen.runtime.strings.emit_string_literal("\n")
            # Convert fat pointer to C string for printf
            newline_ptr = self.codegen.runtime.strings.emit_to_cstr(newline_struct)
            self.codegen.builder.call(self.codegen.runtime.libc_stdio.printf, [newline_ptr])

    def emit_integer_to_string(self, int_value: ir.Value, is_signed: bool, bit_width: int) -> ir.Value:
        """Generate integer to string conversion using sprintf.

        Args:
            int_value: Integer value to convert.
            is_signed: True for signed integers, False for unsigned.
            bit_width: Bit width of the integer type.

        Returns:
            Pointer to newly allocated string containing the integer representation.

        Raises:
            AssertionError: If required runtime functions are not declared.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if self.codegen.runtime.libc_strings.sprintf is None:
            raise_internal_error("CE0013", name="sprintf")
        # Choose appropriate format string based on type
        if bit_width <= 32:
            if is_signed:
                fmt_str = self._get_format_string("i32", FORMAT_STRINGS["i32"])
            else:
                fmt_str = self._get_format_string("u32", FORMAT_STRINGS["u32"])
        else:  # 64-bit
            if is_signed:
                fmt_str = self._get_format_string("i64", FORMAT_STRINGS["i64"])
            else:
                fmt_str = self._get_format_string("u64", FORMAT_STRINGS["u64"])

        # Allocate buffer for the string (32 bytes should be enough for any integer)
        buffer = self._allocate_conversion_buffer(32)

        # Convert value to appropriate type for sprintf
        converted_value = self._prepare_integer_for_sprintf(int_value, is_signed, bit_width)

        # Call sprintf
        self.codegen.builder.call(self.codegen.runtime.libc_strings.sprintf, [buffer, fmt_str, converted_value])

        # Convert C string to fat pointer struct
        return self.codegen.runtime.strings.emit_cstr_to_fat_pointer(buffer)

    def emit_float_to_string(self, float_value: ir.Value, is_double: bool) -> ir.Value:
        """Generate float to string conversion using sprintf.

        Args:
            float_value: Float value to convert.
            is_double: True for f64, False for f32.

        Returns:
            Pointer to newly allocated string containing the float representation.

        Raises:
            AssertionError: If required runtime functions are not declared.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if self.codegen.runtime.libc_strings.sprintf is None:
            raise_internal_error("CE0013", name="sprintf")
        # Choose appropriate format string
        if is_double:
            fmt_str = self._get_format_string("f64", "%.6f")
        else:
            fmt_str = self._get_format_string("f32", "%.6f")
            # Extend f32 to f64 for sprintf
            float_value = self.codegen.builder.fpext(float_value, self.codegen.types.f64)

        # Allocate buffer for the string (64 bytes should be enough for any float)
        buffer = self._allocate_conversion_buffer(64)

        # Call sprintf
        self.codegen.builder.call(self.codegen.runtime.libc_strings.sprintf, [buffer, fmt_str, float_value])

        # Convert C string to fat pointer struct
        return self.codegen.runtime.strings.emit_cstr_to_fat_pointer(buffer)

    def emit_bool_to_string(self, bool_value: ir.Value) -> ir.Value:
        """Generate bool to string conversion.

        Args:
            bool_value: Bool value to convert (i1 or i8).

        Returns:
            Fat pointer struct to "true" or "false".
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        # Emit fat pointer structs for true/false
        true_str = self.codegen.runtime.strings.emit_string_literal(FORMAT_STRINGS["bool_true"])
        false_str = self.codegen.runtime.strings.emit_string_literal(FORMAT_STRINGS["bool_false"])

        # Convert to i1 if needed
        if bool_value.type != self.codegen.i1:
            bool_value = self.codegen.utils.as_i1(bool_value)

        # Use select to choose between true/false fat pointer structs
        # In modern LLVM, select works on aggregate types like structs
        return self.codegen.builder.select(bool_value, true_str, false_str)

    def emit_character_case_conversion(self, char_value: ir.Value, to_upper: bool) -> ir.Value:
        """Generate toupper/tolower call for character case conversion.

        Args:
            char_value: Character value (as i32).
            to_upper: True for uppercase, False for lowercase.

        Returns:
            Converted character value (as i32).

        Raises:
            AssertionError: If required runtime functions are not declared.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if to_upper:
            if self.codegen.runtime.libc_ctype.toupper is None:
                raise_internal_error("CE0013", name="toupper")
            return self.codegen.builder.call(self.codegen.runtime.libc_ctype.toupper, [char_value])
        else:
            if self.codegen.runtime.libc_ctype.tolower is None:
                raise_internal_error("CE0013", name="tolower")
            return self.codegen.builder.call(self.codegen.runtime.libc_ctype.tolower, [char_value])

    def emit_character_classification(self, char_value: ir.Value, classification: str) -> ir.Value:
        """Generate character classification call (isspace, isdigit, isalpha, isalnum).

        Args:
            char_value: Character value (as i32).
            classification: Type of classification ("space", "digit", "alpha", "alnum").

        Returns:
            Non-zero if character matches classification, zero otherwise.

        Raises:
            AssertionError: If required runtime functions are not declared.
            ValueError: If classification type is not supported.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        classification_funcs = {
            "space": self.codegen.runtime.libc_ctype.isspace,
            "digit": self.codegen.runtime.libc_ctype.isdigit,
            "alpha": self.codegen.runtime.libc_ctype.isalpha,
            "alnum": self.codegen.runtime.libc_ctype.isalnum,
        }

        if classification not in classification_funcs:
            raise_internal_error("CE0096", operation=classification)

        func = classification_funcs[classification]
        if func is None:
            raise_internal_error("CE0013", name=classification)
        return self.codegen.builder.call(func, [char_value])

    def _create_format_string(self, name: str, format_str: str) -> ir.GlobalVariable:
        """Generic method to create a global format string constant.

        Creates a private, read-only global variable containing the
        null-terminated format string.

        Args:
            name: The format string name (e.g., "i32", "f64").
            format_str: The actual format string (e.g., "%d", "%.6f").

        Returns:
            The created global variable.
        """
        data = format_str.encode('utf-8') + b'\0'
        arr_ty = ir.ArrayType(self.codegen.i8, len(data))
        gv = ir.GlobalVariable(self.codegen.module, arr_ty, name=f".fmt.{name}")
        gv.linkage = "private"
        gv.global_constant = True
        gv.unnamed_addr = "unnamed_addr"
        gv.initializer = ir.Constant(arr_ty, bytearray(data))
        return gv

    def _get_format_string(self, name: str, format_str: str) -> ir.Value:
        """Get or create a global format string constant.

        Uses the centralized _create_format_string() method to eliminate
        code duplication. Caches the result in an instance attribute.

        Args:
            name: Name of the format string (e.g., "i32", "f64").
            format_str: The actual format string (e.g., "%d", "%.6f").

        Returns:
            Pointer to the global format string constant.
        """
        attr_name = f"fmt_{name}"
        existing = getattr(self, attr_name, None)

        if existing is None:
            # Use the centralized format string creation method
            global_str = self._create_format_string(name, format_str)
            setattr(self, attr_name, global_str)
            existing = global_str

        # Return GEP to get char* pointer
        zero = ir.Constant(self.codegen.i32, 0)
        return self.codegen.builder.gep(existing, [zero, zero])

    def _allocate_conversion_buffer(self, size: int) -> ir.Value:
        """Allocate a buffer for type-to-string conversion.

        Args:
            size: Buffer size in bytes.

        Returns:
            Pointer to allocated buffer.
        """
        malloc_func = self.codegen.get_malloc_func()
        buffer_size = make_i64_const(size)
        return self.codegen.builder.call(malloc_func, [buffer_size])

    def _prepare_integer_for_sprintf(
        self, int_value: ir.Value, is_signed: bool, bit_width: int
    ) -> ir.Value:
        """Prepare integer value for sprintf by converting to appropriate type.

        Args:
            int_value: Integer value to convert.
            is_signed: True for signed integers, False for unsigned.
            bit_width: Bit width of the integer type.

        Returns:
            Converted integer value suitable for sprintf.
        """
        if bit_width < 32:
            # Extend smaller integers to 32-bit for sprintf
            if is_signed:
                return self.codegen.builder.sext(int_value, self.codegen.i32)
            else:
                return self.codegen.builder.zext(int_value, self.codegen.i32)
        else:
            # 32-bit or 64-bit, use as-is
            return int_value
