"""String constant management and deduplication.

This module manages string literals in LLVM IR, ensuring each unique
string is only defined once for efficient code generation.

All string constant creation in the backend should go through this manager
to ensure proper deduplication and consistent naming.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Tuple

from llvmlite import ir

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class StringConstantManager:
    """Manages string constants with content-based deduplication.

    This class provides a centralized cache for string literals used throughout
    the compiled program. Each unique string is only emitted once as a global
    constant, reducing code size and improving efficiency.

    Uses a Dict[str, GlobalVariable] cache for O(1) lookup of existing strings.
    All string constant creation should go through this manager.
    """

    def __init__(self, codegen: 'LLVMCodegen'):
        """Initialize the string constant manager.

        Args:
            codegen: The LLVM code generator instance.
        """
        self.codegen = codegen
        # Content-based cache: string content -> global variable
        self._cache: Dict[str, ir.GlobalVariable] = {}

    def _make_global_name(self, value: str, null_terminated: bool) -> str:
        """Generate a content-based unique name for a string constant.

        Uses hash of content to ensure same string has same name across modules,
        which is important for linker deduplication.

        Args:
            value: The string value.
            null_terminated: Whether this is null-terminated.

        Returns:
            Unique name for the global variable.
        """
        suffix = "nt" if null_terminated else "raw"
        content_hash = hash(value) & 0xFFFFFFFF
        return f".str.{len(value)}_{content_hash}_{suffix}"

    def get_or_create(self, value: str, null_terminated: bool = False) -> ir.GlobalVariable:
        """Get existing or create new string constant with deduplication.

        This is the primary method for string constant creation. All other
        methods delegate to this one.

        Args:
            value: String literal value.
            null_terminated: If True, append null terminator to the string.

        Returns:
            Global variable containing string data.
        """
        # Create cache key including null-termination flag
        key_str = f"{value}|{'nt' if null_terminated else 'raw'}"

        if key_str in self._cache:
            return self._cache[key_str]

        # Generate content-based name for cross-module deduplication
        global_name = self._make_global_name(value, null_terminated)

        # Check if already exists in module (from linked library)
        existing = self.codegen.module.globals.get(global_name)
        if existing is not None:
            self._cache[key_str] = existing
            return existing

        # Encode string data
        string_data = bytearray(value.encode('utf-8'))
        if null_terminated:
            string_data.append(0)

        # Create LLVM constant
        i8 = self.codegen.types.i8
        const_type = ir.ArrayType(i8, len(string_data))
        const_value = ir.Constant(const_type, string_data)

        # Create global variable with content-based name
        global_var = ir.GlobalVariable(
            self.codegen.module,
            const_type,
            name=global_name
        )

        global_var.initializer = const_value
        global_var.global_constant = True
        global_var.linkage = 'private'
        global_var.unnamed_addr = True

        self._cache[key_str] = global_var
        return global_var

    def get_or_create_string_constant(self, value: str) -> ir.GlobalVariable:
        """Get existing or create new null-terminated string constant.

        Legacy method for backward compatibility. Use get_or_create() for new code.

        Args:
            value: String literal value.

        Returns:
            Global variable containing null-terminated string data.
        """
        return self.get_or_create(value, null_terminated=True)

    def get_or_create_raw(self, value: str) -> ir.GlobalVariable:
        """Get existing or create new string constant WITHOUT null terminator.

        Used for fat pointer strings that store length separately.

        Args:
            value: String literal value.

        Returns:
            Global variable containing string data (no null terminator).
        """
        return self.get_or_create(value, null_terminated=False)

    def get_string_ptr(self, value: str, null_terminated: bool = False) -> Tuple[ir.GlobalVariable, ir.Value]:
        """Get string constant and a pointer to its first element.

        Convenience method that returns both the global and a GEP pointer.

        Args:
            value: String literal value.
            null_terminated: If True, append null terminator to the string.

        Returns:
            Tuple of (global_var, pointer_to_first_element).
        """
        global_var = self.get_or_create(value, null_terminated)
        zero = ir.Constant(self.codegen.i32, 0)
        ptr = self.codegen.builder.gep(global_var, [zero, zero], name="str_ptr")
        return global_var, ptr

    def create_string_constant(self, name: str, value: str) -> ir.GlobalVariable:
        """Create a named string constant (uses deduplication).

        The name parameter is ignored - deduplication is based on content.
        Kept for backward compatibility.

        Args:
            name: Ignored (legacy parameter).
            value: String value.

        Returns:
            The global variable containing the string array.
        """
        return self.get_or_create(value, null_terminated=True)

    def clear(self):
        """Clear the string cache."""
        self._cache.clear()

    @property
    def stats(self) -> Dict[str, int]:
        """Return deduplication statistics.

        Returns:
            Dict with 'unique_strings' count.
        """
        return {'unique_strings': len(self._cache)}
