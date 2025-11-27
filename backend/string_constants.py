"""String constant management and deduplication.

This module manages string literals in LLVM IR, ensuring each unique
string is only defined once for efficient code generation.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Dict

from llvmlite import ir

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class StringConstantManager:
    """Manages string constants with deduplication.

    This class provides a centralized cache for string literals used throughout
    the compiled program. Each unique string is only emitted once as a global
    constant, reducing code size and improving efficiency.
    """

    def __init__(self, codegen: LLVMCodegen):
        """Initialize the string constant manager.

        Args:
            codegen: The LLVM code generator instance.
        """
        self.codegen = codegen
        self._string_cache: Dict[str, ir.GlobalVariable] = {}

    def create_string_constant(self, name: str, value: str) -> ir.GlobalVariable:
        """Create a global string constant without requiring a builder context.

        Args:
            name: Name of the constant.
            value: String value.

        Returns:
            The global variable containing the string array.
        """
        null_terminated = value + '\0'
        str_bytes = bytearray(null_terminated, 'utf-8')
        array_type = ir.ArrayType(self.codegen.i8, len(str_bytes))

        global_name = f".str_const.{name}"
        string_global = ir.GlobalVariable(self.codegen.module, array_type, name=global_name)
        string_global.linkage = 'internal'
        string_global.global_constant = True
        string_global.initializer = ir.Constant(array_type, str_bytes)
        string_global.unnamed_addr = True

        return string_global

    def get_or_create_string_constant(self, value: str) -> ir.GlobalVariable:
        """Get existing or create new string constant with deduplication.

        This method implements a cache to ensure each unique string literal
        is only defined once in the LLVM module.

        Args:
            value: String literal value.

        Returns:
            Global variable containing string data.
        """
        if value in self._string_cache:
            return self._string_cache[value]

        # Create new constant
        i8 = self.codegen.types.i8
        string_data = bytearray(value.encode('utf-8'))
        string_data.append(0)

        const_type = ir.ArrayType(i8, len(string_data))
        const_value = ir.Constant(const_type, string_data)

        global_var = ir.GlobalVariable(
            self.codegen.module,
            const_type,
            name=f".str.{len(self._string_cache)}"
        )
        global_var.initializer = const_value
        global_var.global_constant = True
        global_var.linkage = 'private'

        self._string_cache[value] = global_var
        return global_var

    def clear(self):
        """Clear the string cache."""
        self._string_cache.clear()
