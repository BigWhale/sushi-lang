"""LLVM utility functions for casting, helpers, and general-purpose operations."""
from __future__ import annotations
from typing import List, TYPE_CHECKING

from llvmlite import ir
from sushi_lang.internals.report import Span
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class LLVMUtils:
    """Utility class providing casting and helper operations for LLVM code generation."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize utility class with reference to main codegen instance."""
        self.codegen = codegen

    def as_i1(self, v: ir.Value) -> ir.Value:
        """Convert value to i1 (boolean) for conditional expressions."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        ty = v.type

        if isinstance(ty, ir.IntType) and ty.width == 1:
            return v

        if isinstance(ty, ir.IntType):
            return self.codegen.builder.icmp_unsigned('!=', v, ir.Constant(ty, 0))

        if isinstance(ty, ir.PointerType):
            return self.codegen.builder.icmp_unsigned('!=', v, ir.Constant(ty, None))

        # Check for Result<T> enum type: {i32 tag, [K x i64] data} (#300 phase 2)
        # For Result, check if tag == 0 (Ok variant)
        # LiteralStructType on purpose (#257): enums keep their anonymous
        # {i32 tag, [K x i64]} layout -- only user STRUCTS became identified types. A user
        # struct shaped {i32, [K x i64]} must not be read as a Result here.
        if isinstance(ty, ir.LiteralStructType) and len(ty.elements) == 2:
            if isinstance(ty.elements[0], ir.IntType) and ty.elements[0].width == 32:
                if isinstance(ty.elements[1], ir.ArrayType):
                    return enum_utils.check_enum_variant(
                        self.codegen, v, variant_index=0, signed=True, name="is_ok"
                    )

        raise_internal_error("CE0017", src=str(ty), dst="i1")

    def as_i8(self, v: ir.Value) -> ir.Value:
        """Convert integer value to i8 with appropriate width conversion."""
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
        """Determine if an LLVM integer type represents a signed Sushi type."""
        if not isinstance(llvm_type, ir.IntType):
            return False

        if llvm_type.width == 1:
            return False

        # For other types, we assume signed by default since most operations
        # in the language use signed integers
        # Note: The caller should know the actual signedness from context
        return True

    def convert_int_to_i32(self, v: ir.Value, is_signed: bool = True) -> ir.Value:
        """Convert any integer type to i32 with proper signed/unsigned handling."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        ty = v.type

        if not isinstance(ty, ir.IntType):
            raise_internal_error("CE0017", src=str(ty), dst="i32")

        if ty.width == 32:
            return v
        elif ty.width < 32:
            if is_signed:
                return self.codegen.builder.sext(v, self.codegen.i32)
            else:
                return self.codegen.builder.zext(v, self.codegen.i32)
        else:
            return self.codegen.builder.trunc(v, self.codegen.i32)

    def as_i32(self, v: ir.Value) -> ir.Value:
        """Convert integer or float value to i32 with appropriate conversion."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        ty = v.type
        if isinstance(ty, ir.IntType):
            return self.convert_int_to_i32(v, is_signed=False)
        elif isinstance(ty, (ir.FloatType, ir.DoubleType)):
            return self.codegen.builder.fptosi(v, self.codegen.i32)
        raise_internal_error("CE0017", src=str(ty), dst="i32")

    def cast_to_int_width(self, v: ir.Value, dst: ir.IntType, is_signed: bool = False) -> ir.Value:
        """Cast value to target integer width using dispatch table."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")

        width_dispatch = {
            32: lambda: self.as_i32(v),
            8: lambda: self.as_i8(v),
        }

        if dst.width in width_dispatch:
            return width_dispatch[dst.width]()

        if isinstance(v.type, ir.IntType):
            if v.type.width < dst.width:
                if is_signed:
                    return self.codegen.builder.sext(v, dst)
                else:
                    return self.codegen.builder.zext(v, dst)
            elif v.type.width > dst.width:
                return self.codegen.builder.trunc(v, dst)
            else:
                return v

        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def cast_for_param(self, v: ir.Value, dst: ir.Type) -> ir.Value:
        """Cast expression value to match function parameter type."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        if v.type == dst:
            return v

        if isinstance(dst, ir.IntType):
            return self._cast_to_int_width(v, dst)

        if isinstance(dst, (ir.FloatType, ir.DoubleType)):
            return self._cast_to_float(v, dst)

        if isinstance(dst, ir.PointerType):
            return self._cast_to_pointer(v, dst)

        if isinstance(dst, ir.ArrayType):
            return self._cast_to_array(v, dst)

        # Struct type checks (exact match required - no conversion between different struct
        # types). BaseStructType so a user struct's identified type (#257) reports the
        # specific "cannot convert struct to struct" diagnostic rather than the generic one.
        if isinstance(dst, ir.types.BaseStructType) and isinstance(v.type, ir.types.BaseStructType):
            raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def _cast_to_int_width(self, v: ir.Value, dst: ir.IntType) -> ir.Value:
        """Cast value to target integer width using dispatch table."""
        return self.cast_to_int_width(v, dst)

    def _cast_to_float(self, v: ir.Value, dst: ir.Type) -> ir.Value:
        """Cast value to target float type."""
        if isinstance(v.type, ir.IntType):
            return self.codegen.builder.sitofp(v, dst)
        elif isinstance(v.type, (ir.FloatType, ir.DoubleType)):
            if isinstance(dst, ir.DoubleType) and isinstance(v.type, ir.FloatType):
                return self.codegen.builder.fpext(v, dst)
            elif isinstance(dst, ir.FloatType) and isinstance(v.type, ir.DoubleType):
                return self.codegen.builder.fptrunc(v, dst)
        raise_internal_error("CE0017", src=str(v.type), dst=str(dst))

    def _cast_to_pointer(self, v: ir.Value, dst: ir.PointerType) -> ir.Value:
        """Cast value to pointer type (mainly for string pointers)."""
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
        """Get pointer to first character of a global C-string constant."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        zero32 = ir.Constant(self.codegen.i32, 0)
        return self.codegen.builder.gep(gv, [zero32, zero32], inbounds=True)

    def ensure_open_block(self) -> None:
        """Ensure the current basic block is not terminated."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        blk = self.codegen.builder.block
        if blk is None or blk.terminator is not None:
            raise_internal_error("CE0059")

    def after_terminator_unreachable(self) -> None:
        """Create unreachable block after a terminator instruction."""
        fn = self.codegen.builder.function
        nxt = fn.append_basic_block(name="unreachable")
        self.codegen.builder.position_at_end(nxt)

    @staticmethod
    def loc_str(span: Span | None) -> str:
        """Format source location span as human-readable string."""
        if not span:
            return ""
        return f" at {span.line}:{span.col}"

    def block_statements(self, blk) -> List:
        """Extract statement list from a block node."""
        stmts = getattr(blk, "statements", None)
        if isinstance(stmts, list):
            return stmts
        raise_internal_error("CE0061")

    def get_zero_value(self, llvm_type: ir.Type) -> ir.Value:
        """Create a zero/default value for a given LLVM type."""
        if isinstance(llvm_type, ir.IntType):
            return ir.Constant(llvm_type, 0)

        if isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(llvm_type, 0.0)

        if isinstance(llvm_type, ir.PointerType):
            return ir.Constant(llvm_type, None)

        if isinstance(llvm_type, ir.ArrayType):
            element_zero = self.get_zero_value(llvm_type.element)
            return ir.Constant(llvm_type, [element_zero] * llvm_type.count)

        # Struct types -> {0, 0, ...}
        # BaseStructType covers both the anonymous fat pointers and a user struct's
        # *identified* type (`%Tree`, #257), which is a sibling of LiteralStructType, not a
        # subclass. Narrower, this fell through to the ir.Undefined below and every user
        # struct was zero-initialised with undef -- garbage, and silent.
        if isinstance(llvm_type, ir.types.BaseStructType):
            field_zeros = [self.get_zero_value(field_type) for field_type in llvm_type.elements]
            return ir.Constant(llvm_type, field_zeros)

        return ir.Undefined(llvm_type)
