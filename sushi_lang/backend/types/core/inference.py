"""Type inference from LLVM IR types back to Sushi language types."""
from __future__ import annotations

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error


class TypeInference:
    """Infers Sushi semantic types from LLVM IR types."""

    def __init__(self, i8: ir.IntType, i32: ir.IntType, string_struct: ir.LiteralStructType):
        """Initialize type inference with LLVM type references."""
        self.i8 = i8
        self.i32 = i32
        self.string_struct = string_struct

        # Reverse type mapping for O(1) LLVM -> language type conversion
        # Note: i8 is ambiguous (could be i8, u8, or bool) - default to i8
        self._llvm_to_lang_type_map: dict[ir.Type, str] = {
            ir.IntType(8): "i8",
            ir.IntType(16): "i16",
            ir.IntType(32): "i32",
            ir.IntType(64): "i64",
            ir.FloatType(): "f32",
            ir.DoubleType(): "f64",
        }

        self._llvm_to_lang_type_cache: dict[ir.Type, str] = {}

    def infer_llvm_type_from_value(self, value: ir.Value) -> ir.Type:
        """Infer LLVM type from a runtime value for method dispatch."""
        return value.type

    def map_llvm_to_language_type(self, llvm_type: ir.Type) -> str:
        """Map LLVM type back to language type name for method resolution."""
        if llvm_type in self._llvm_to_lang_type_cache:
            return self._llvm_to_lang_type_cache[llvm_type]

        if llvm_type in self._llvm_to_lang_type_map:
            return self._llvm_to_lang_type_map[llvm_type]

        result = None

        if self.is_string_type(llvm_type):
            result = "string"

        elif isinstance(llvm_type, ir.ArrayType):
            element_name = self.map_llvm_to_language_type(llvm_type.element)
            result = f"{element_name}[{llvm_type.count}]"

        elif self.is_dynamic_array_type(llvm_type):
            data_field = llvm_type.elements[2]  # T*
            element_type = data_field.pointee
            element_name = self.map_llvm_to_language_type(element_type)
            result = f"{element_name}[]"

        elif isinstance(llvm_type, ir.PointerType) and self.is_dynamic_array_type(llvm_type.pointee):
            pointee = llvm_type.pointee
            data_field = pointee.elements[2]  # T*
            element_type = data_field.pointee
            element_name = self.map_llvm_to_language_type(element_type)
            result = f"{element_name}[]"

        if result is not None:
            self._llvm_to_lang_type_cache[llvm_type] = result
            return result

        raise_internal_error("CE0019", llvm_type=str(llvm_type))

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

    def is_dynamic_array_type(self, llvm_type: ir.Type) -> bool:
        """Check if an LLVM type represents a dynamic array struct."""
        # LiteralStructType on purpose (#257): a dynamic array is an ANONYMOUS descriptor.
        # This is the sharpest case of the shape collision -- `struct S: i32 a; i32 b; ptr p`
        # is exactly {i32, i32, T*}, and while user structs were literal it answered True
        # here and was handled as a dynamic array. Do not widen.
        if not isinstance(llvm_type, ir.LiteralStructType):
            return False

        elements = llvm_type.elements
        if len(elements) != 3:
            return False

        return (
            elements[0] == self.i32 and
            elements[1] == self.i32 and
            isinstance(elements[2], ir.PointerType)
        )

    def is_integer_type(self, llvm_type: ir.Type, width: int | None = None) -> bool:
        """Check if LLVM type is an integer with optional width constraint."""
        if not isinstance(llvm_type, ir.IntType):
            return False
        if width is not None:
            return llvm_type.width == width
        return True
