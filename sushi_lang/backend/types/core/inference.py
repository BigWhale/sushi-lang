"""Type inference from LLVM IR types back to Sushi language types.

This module provides reverse mapping for introspection, method resolution,
and debugging purposes.
"""
from __future__ import annotations

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error


class TypeInference:
    """Infers Sushi semantic types from LLVM IR types."""

    def __init__(self, i8: ir.IntType, i32: ir.IntType, string_struct: ir.LiteralStructType):
        """Initialize type inference with LLVM type references.

        Args:
            i8: LLVM i8 type reference
            i32: LLVM i32 type reference
            string_struct: LLVM string struct type reference
        """
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

        # Cache for complex type mappings (arrays, dynamic arrays, etc.)
        self._llvm_to_lang_type_cache: dict[ir.Type, str] = {}

    def infer_llvm_type_from_value(self, value: ir.Value) -> ir.Type:
        """Infer LLVM type from a runtime value for method dispatch.

        Used primarily for extension method resolution where we need to
        determine the receiver type from an LLVM value.

        Args:
            value: The LLVM value to analyze.

        Returns:
            The LLVM type of the value.
        """
        return value.type

    def map_llvm_to_language_type(self, llvm_type: ir.Type) -> str:
        """Map LLVM type back to language type name for method resolution.

        Converts LLVM IR types back to language type names, primarily used
        for generating extension method function names. Uses a pre-built
        dictionary for O(1) lookup on simple types and caches complex types.

        Args:
            llvm_type: The LLVM IR type to map.

        Returns:
            The corresponding language type name.

        Raises:
            TypeError: If the LLVM type cannot be mapped to a language type.
        """
        # Check cache first (covers both simple types and previously computed complex types)
        if llvm_type in self._llvm_to_lang_type_cache:
            return self._llvm_to_lang_type_cache[llvm_type]

        # Fast path: Direct type mapping (O(1) lookup)
        if llvm_type in self._llvm_to_lang_type_map:
            return self._llvm_to_lang_type_map[llvm_type]

        # Compute and cache complex types
        result = None

        # String type check (fat pointer struct)
        if self.is_string_type(llvm_type):
            result = "string"

        # Array types (require recursion)
        elif isinstance(llvm_type, ir.ArrayType):
            element_name = self.map_llvm_to_language_type(llvm_type.element)
            result = f"{element_name}[{llvm_type.count}]"

        # Dynamic array types
        elif self.is_dynamic_array_type(llvm_type):
            data_field = llvm_type.elements[2]  # T*
            element_type = data_field.pointee
            element_name = self.map_llvm_to_language_type(element_type)
            result = f"{element_name}[]"

        # Pointer to dynamic array (from GEP on struct fields)
        elif isinstance(llvm_type, ir.PointerType) and self.is_dynamic_array_type(llvm_type.pointee):
            pointee = llvm_type.pointee
            data_field = pointee.elements[2]  # T*
            element_type = data_field.pointee
            element_name = self.map_llvm_to_language_type(element_type)
            result = f"{element_name}[]"

        if result is not None:
            # Cache the result for future lookups
            self._llvm_to_lang_type_cache[llvm_type] = result
            return result

        raise_internal_error("CE0019", llvm_type=str(llvm_type))

    def is_string_type(self, llvm_type: ir.Type) -> bool:
        """Check if LLVM type represents a string (fat pointer struct).

        Args:
            llvm_type: The LLVM type to check.

        Returns:
            True if the type is a string fat pointer struct, False otherwise.
        """
        if not isinstance(llvm_type, ir.LiteralStructType):
            return False

        # Check struct layout: {i8*, i32}
        elements = llvm_type.elements
        if len(elements) != 2:
            return False

        # Check field types
        return (
            isinstance(elements[0], ir.PointerType) and
            isinstance(elements[0].pointee, ir.IntType) and
            elements[0].pointee.width == 8 and
            elements[1] == self.i32
        )

    def is_dynamic_array_type(self, llvm_type: ir.Type) -> bool:
        """Check if an LLVM type represents a dynamic array struct.

        Args:
            llvm_type: The LLVM type to check.

        Returns:
            True if the type is a dynamic array struct, False otherwise.
        """
        if not isinstance(llvm_type, ir.LiteralStructType):
            return False

        # Check struct layout: {i32, i32, T*}
        elements = llvm_type.elements
        if len(elements) != 3:
            return False

        # Check field types
        return (
            elements[0] == self.i32 and
            elements[1] == self.i32 and
            isinstance(elements[2], ir.PointerType)
        )

    def is_integer_type(self, llvm_type: ir.Type, width: int | None = None) -> bool:
        """Check if LLVM type is an integer with optional width constraint.

        Args:
            llvm_type: The LLVM type to check.
            width: Optional specific bit width to match.

        Returns:
            True if the type is an integer of the specified width, False otherwise.
        """
        if not isinstance(llvm_type, ir.IntType):
            return False
        if width is not None:
            return llvm_type.width == width
        return True
