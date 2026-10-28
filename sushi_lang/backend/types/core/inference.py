"""Type inference from LLVM IR types back to Sushi language types."""
from __future__ import annotations

from llvmlite import ir


class TypeInference:
    """Infers Sushi semantic types from LLVM IR types."""

    def __init__(self, i8: ir.IntType, i32: ir.IntType, string_struct: ir.LiteralStructType):
        """Initialize type inference with LLVM type references."""
        self.i8 = i8
        self.i32 = i32
        self.string_struct = string_struct

    def is_string_type(self, llvm_type: ir.Type) -> bool:
        """Check if LLVM type represents a string (fat pointer struct)."""
        # LiteralStructType on purpose (#257): a string is an ANONYMOUS fat pointer, never a
        # named type. Widening this to BaseStructType would make a user struct shaped
        # {i8*, i32, i8} answer True and be treated as a string -- the shape-collision class
        # that giving user structs their own identified types removes.
        if not isinstance(llvm_type, ir.LiteralStructType):
            return False

        # Check struct layout: {i8* data, i32 size, i8 owned}  (owned = RAII bit, #145)
        elements = llvm_type.elements
        if len(elements) != 3:
            return False

        return (
            isinstance(elements[0], ir.PointerType) and
            isinstance(elements[0].pointee, ir.IntType) and
            elements[0].pointee.width == 8 and
            elements[1] == self.i32 and
            isinstance(elements[2], ir.IntType) and
            elements[2].width == 8
        )

    def is_integer_type(self, llvm_type: ir.Type, width: int | None = None) -> bool:
        """Check if LLVM type is an integer with optional width constraint."""
        if not isinstance(llvm_type, ir.IntType):
            return False
        if width is not None:
            return llvm_type.width == width
        return True
