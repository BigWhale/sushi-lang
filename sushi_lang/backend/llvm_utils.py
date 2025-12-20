"""
LLVM utility functions for casting, helpers, and general-purpose operations.

This module provides utility functions used throughout the LLVM backend,
including type casting, value conversion, and various helper operations
that support code generation.
"""
from __future__ import annotations
from typing import List, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.internals.report import Span
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils

if TYPE_CHECKING:
    from sushi_lang.backend.interfaces import CodegenProtocol


class LLVMUtils:
    """Utility class providing casting and helper operations for LLVM code generation."""

    def __init__(self, codegen: 'CodegenProtocol') -> None:
        """Initialize utility class with reference to main codegen instance.

        Args:
            codegen: The main codegen instance providing context and builders.
        """
        self.codegen = codegen

    def as_i1(self, v: ir.Value) -> ir.Value:
        """Convert value to i1 (boolean) for conditional expressions.

        Converts various LLVM types to i1 for use in conditional contexts
        like if statements and logical operations. Integer types are compared
        against zero, pointer types are compared against null, and Result<T>
        enums check if the tag is 0 (Ok variant).

        Args:
            v: The LLVM value to convert to boolean.

        Returns:
            An i1 value representing the truthiness of the input.

        Raises:
            TypeError: If the value type cannot be converted to boolean.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        ty = v.type

        if isinstance(ty, ir.IntType) and ty.width == 1:
            return v

        if isinstance(ty, ir.IntType):
            return self.codegen.builder.icmp_unsigned('!=', v, ir.Constant(ty, 0))

        if isinstance(ty, ir.PointerType):
            return self.codegen.builder.icmp_unsigned('!=', v, ir.Constant(ty, None))

        # Check for Result<T> enum type: {i32 tag, [N x i8] data}
        # For Result, check if tag == 0 (Ok variant)
        if isinstance(ty, ir.LiteralStructType) and len(ty.elements) == 2:
            # Check if first element is i32 (tag) and second is an array (data)
            if isinstance(ty.elements[0], ir.IntType) and ty.elements[0].width == 32:
                if isinstance(ty.elements[1], ir.ArrayType):
                    # This is a Result<T> enum - extract tag and compare to 0 (Ok variant)
                    return enum_utils.check_enum_variant(
                        self.codegen, v, variant_index=0, signed=True, name="is_ok"
                    )

        raise_internal_error("CE0017", src=str(ty), dst="i1")

    def as_i8(self, v: ir.Value) -> ir.Value:
        """Convert integer value to i8 with appropriate width conversion.

        Performs appropriate casting between integer widths: zero-extends i1,
        truncates i32, passes through i8 unchanged.

        Args:
            v: The integer value to convert to i8.

        Returns:
            An i8 value equivalent to the input.

        Raises:
            TypeError: If the value is not an integer type.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if isinstance(v.type, ir.IntType):
            if v.type.width == 8:
                return v
            if v.type.width == 1:
                return self.codegen.builder.zext(v, self.codegen.i8)
            if v.type.width == 32:
                return self.codegen.builder.trunc(v, self.codegen.i8)
        raise_internal_error("CE0017", src=str(v.type), dst="i8")

    def is_signed_int_type(self, llvm_type: ir.Type) -> bool:
        """Determine if an LLVM integer type represents a signed Sushi type.

        In Sushi, we have explicit signed (i8, i16, i32, i64) and unsigned
        (u8, u16, u32, u64) types. This method helps distinguish between them
        based on the LLVM type width. Since LLVM doesn't encode signedness in
        the type system, we rely on conventions:
        - i1 (bool) is treated as unsigned
        - All other integer types: we check the codegen's type system

        Args:
            llvm_type: The LLVM integer type to check.

        Returns:
            True if the type represents a signed integer, False for unsigned.
            Defaults to True (signed) for ambiguous cases.
        """
        if not isinstance(llvm_type, ir.IntType):
            return False

        # i1 (bool) is treated as unsigned
        if llvm_type.width == 1:
            return False

        # For other types, we assume signed by default since most operations
        # in the language use signed integers
        # Note: The caller should know the actual signedness from context
        return True

    def convert_int_to_i32(self, v: ir.Value, is_signed: bool = True) -> ir.Value:
        """Convert any integer type to i32 with proper signed/unsigned handling.

        This is the canonical method for integer to i32 conversion throughout
        the compiler. It properly handles signed vs unsigned integers by using
        sext for signed types and zext for unsigned types.

        Args:
            v: The integer value to convert to i32.
            is_signed: Whether the source integer is signed (True) or unsigned (False).
                      Defaults to True for backward compatibility.

        Returns:
            An i32 value equivalent to the input.

        Raises:
            TypeError: If the value is not an integer type.

        Examples:
            - i8 -> i32: sign extend (sext)
            - u8 -> i32: zero extend (zext)
            - i64 -> i32: truncate (trunc)
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        ty = v.type

        if not isinstance(ty, ir.IntType):
            raise_internal_error("CE0017", src=str(ty), dst="i32")

        if ty.width == 32:
            return v
        elif ty.width < 32:
            # Extend smaller widths to i32
            if is_signed:
                return self.codegen.builder.sext(v, self.codegen.i32)
            else:
                return self.codegen.builder.zext(v, self.codegen.i32)
        else:
            # Truncate larger widths to i32 (both signed and unsigned use trunc)
            return self.codegen.builder.trunc(v, self.codegen.i32)

    def as_i32(self, v: ir.Value) -> ir.Value:
        """Convert integer or float value to i32 with appropriate conversion.

        Converts i1 and i8 values to i32 using zero-extension, leaves i32
        values unchanged. For floating-point values, converts using truncation
        toward zero (fptosi).

        Note: This method assumes unsigned integers for backward compatibility.
        For proper signed/unsigned handling, use convert_int_to_i32() directly.

        Args:
            v: The integer or floating-point value to convert to i32.

        Returns:
            An i32 value equivalent to the input.

        Raises:
            TypeError: If the value is not an integer or floating-point type.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        ty = v.type
        if isinstance(ty, ir.IntType):
            # Use convert_int_to_i32 with unsigned (zext) for backward compatibility
            return self.convert_int_to_i32(v, is_signed=False)
        elif isinstance(ty, (ir.FloatType, ir.DoubleType)):
            # Convert floating-point to i32 using truncation toward zero
            return self.codegen.builder.fptosi(v, self.codegen.i32)
        raise_internal_error("CE0017", src=str(ty), dst="i32")

    def cast_to_int_width(self, v: ir.Value, dst: ir.IntType, is_signed: bool = False) -> ir.Value:
        """Cast value to target integer width using dispatch table.

        Central method for all integer width conversions throughout the compiler.
        Uses a dispatch table for specific widths (8, 32) and fallback logic for
        other widths (1, 16, 64).

        Args:
            v: The value to cast.
            dst: The target integer type.
            is_signed: Whether to use signed extension (True) or unsigned/zero extension (False).
                      Default is False for backward compatibility.

        Returns:
            The value cast to the target integer width.

        Raises:
            TypeError: If the cast is not supported.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        # Dispatch table for common widths
        width_dispatch = {
            32: lambda: self.as_i32(v),
            8: lambda: self.as_i8(v),
        }

        # Try dispatch table first
        if dst.width in width_dispatch:
            return width_dispatch[dst.width]()

        # Fallback for other integer widths (i1, i16, i64, etc.)
        if isinstance(v.type, ir.IntType):
            if v.type.width < dst.width:
                # Extend: use signed or unsigned extension based on parameter
                if is_signed:
                    return self.codegen.builder.sext(v, dst)
                else:
                    return self.codegen.builder.zext(v, dst)
            elif v.type.width > dst.width:
                # Truncate (same for both signed and unsigned)
                return self.codegen.builder.trunc(v, dst)
            else:
                # Same width, return as-is
                return v

        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def cast_for_param(self, v: ir.Value, dst: ir.Type) -> ir.Value:
        """Cast expression value to match function parameter type.

        Performs appropriate casting using categorized dispatch for cleaner logic.
        Handles integer widths, floats, pointers, and arrays.

        Args:
            v: The value to cast.
            dst: The target LLVM type for the parameter.

        Returns:
            The value cast to the target type.

        Raises:
            TypeError: If the cast is not supported.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        # Fast path: exact type match (no casting needed)
        if v.type == dst:
            return v

        # Integer type conversions (using width-based dispatch)
        if isinstance(dst, ir.IntType):
            return self._cast_to_int_width(v, dst)

        # Floating-point type conversions
        if isinstance(dst, (ir.FloatType, ir.DoubleType)):
            return self._cast_to_float(v, dst)

        # Pointer type checks (string pointers)
        if isinstance(dst, ir.PointerType):
            return self._cast_to_pointer(v, dst)

        # Array type checks (exact match required)
        if isinstance(dst, ir.ArrayType):
            return self._cast_to_array(v, dst)

        # Struct type checks (exact match required - no conversion between different struct types)
        if isinstance(dst, ir.LiteralStructType) and isinstance(v.type, ir.LiteralStructType):
            # If both are structs and structurally identical, no cast needed (caught by fast path above)
            # If they differ, this is a type error that shouldn't happen after semantic analysis
            raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def _cast_to_int_width(self, v: ir.Value, dst: ir.IntType) -> ir.Value:
        """Cast value to target integer width using dispatch table."""
        return self.cast_to_int_width(v, dst)

    def _cast_to_float(self, v: ir.Value, dst: ir.Type) -> ir.Value:
        """Cast value to target float type."""
        if isinstance(v.type, ir.IntType):
            # Integer to float
            return self.codegen.builder.sitofp(v, dst)
        elif isinstance(v.type, (ir.FloatType, ir.DoubleType)):
            # Float to float conversions
            if isinstance(dst, ir.DoubleType) and isinstance(v.type, ir.FloatType):
                return self.codegen.builder.fpext(v, dst)
            elif isinstance(dst, ir.FloatType) and isinstance(v.type, ir.DoubleType):
                return self.codegen.builder.fptrunc(v, dst)
        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def _cast_to_pointer(self, v: ir.Value, dst: ir.PointerType) -> ir.Value:
        """Cast value to pointer type (mainly for string pointers)."""
        # Check for i8* (string pointer)
        if isinstance(dst.pointee, ir.IntType) and dst.pointee.width == 8:
            if isinstance(v.type, ir.PointerType) and isinstance(v.type.pointee, ir.IntType) and v.type.pointee.width == 8:
                return v
        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def _cast_to_array(self, v: ir.Value, dst: ir.ArrayType) -> ir.Value:
        """Cast value to array type (requires exact match)."""
        if isinstance(v.type, ir.ArrayType):
            if v.type.element == dst.element and v.type.count == dst.count:
                return v
        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def cstr_ptr(self, gv: ir.GlobalVariable) -> ir.Value:
        """Get pointer to first character of a global C-string constant.

        Creates a GEP instruction to get an i8* pointer to the first character
        of a global string array, suitable for passing to C library functions.

        Args:
            gv: Global variable containing the string array.

        Returns:
            An i8* pointer to the first character of the string.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        zero32 = ir.Constant(self.codegen.i32, 0)
        return self.codegen.builder.gep(gv, [zero32, zero32], inbounds=True)

    def ensure_open_block(self) -> None:
        """Ensure the current basic block is not terminated.

        Verifies that the current basic block can accept new instructions
        by checking that it has no terminator instruction.

        Raises:
            RuntimeError: If the current block is None or already terminated.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        blk = self.codegen.builder.block
        if blk is None or blk.terminator is not None:
            raise_internal_error("CE0059")

    def after_terminator_unreachable(self) -> None:
        """Create unreachable block after a terminator instruction.

        Creates a new unreachable basic block and positions the builder there
        to handle subsequent statements that come after control flow terminators
        like break or continue statements.
        """
        fn = self.codegen.builder.function
        nxt = fn.append_basic_block(name="unreachable")
        self.codegen.builder.position_at_end(nxt)

    @staticmethod
    def loc_str(span: Span | None) -> str:
        """Format source location span as human-readable string.

        Converts a source code span into a readable location string for
        error messages and debugging output.

        Args:
            span: The source location span, or None if unavailable.

        Returns:
            Formatted location string, or empty string if span is None.
        """
        if not span:
            return ""
        return f" at {span.line}:{span.col}"

    def block_statements(self, blk) -> List:
        """Extract statement list from a block node.

        Safely extracts the statements list from a block AST node with
        proper error handling for malformed blocks.

        Args:
            blk: The block AST node containing statements.

        Returns:
            List of statement nodes from the block.

        Raises:
            TypeError: If the block does not have a valid statements list.
        """
        stmts = getattr(blk, "statements", None)
        if isinstance(stmts, list):
            return stmts
        raise_internal_error("CE0061")

    def get_zero_value(self, llvm_type: ir.Type) -> ir.Value:
        """Create a zero/default value for a given LLVM type.

        Used for generating default values for Result<T> Err() returns.

        Args:
            llvm_type: The LLVM type for which to create a zero value.

        Returns:
            An LLVM constant zero value of the appropriate type.
        """
        # Integer types -> 0
        if isinstance(llvm_type, ir.IntType):
            return ir.Constant(llvm_type, 0)

        # Floating-point types -> 0.0
        if isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(llvm_type, 0.0)

        # Pointer types -> null
        if isinstance(llvm_type, ir.PointerType):
            return ir.Constant(llvm_type, None)

        # Array types -> [0, 0, ..., 0]
        if isinstance(llvm_type, ir.ArrayType):
            element_zero = self.get_zero_value(llvm_type.element)
            return ir.Constant(llvm_type, [element_zero] * llvm_type.count)

        # Struct types -> {0, 0, ...}
        if isinstance(llvm_type, ir.LiteralStructType):
            field_zeros = [self.get_zero_value(field_type) for field_type in llvm_type.elements]
            return ir.Constant(llvm_type, field_zeros)

        # Fallback for unknown types - create undef
        return ir.Undefined(llvm_type)