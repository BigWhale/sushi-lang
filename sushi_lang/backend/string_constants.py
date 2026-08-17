"""String constant management and deduplication."""
from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Tuple

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class StringConstantManager:
    """Manages string constants with content-based deduplication."""

    def __init__(self, codegen: 'LLVMCodegen'):
        """Initialize the string constant manager."""
        self.codegen = codegen
        # Content-based cache: string content -> global variable
        self._cache: Dict[str, ir.GlobalVariable] = {}

    def _make_global_name(self, value: str, null_terminated: bool) -> str:
        """Generate a content-based unique name for a string constant."""
        suffix = "nt" if null_terminated else "raw"
        content_hash = hash(value) & 0xFFFFFFFF
        return f".str.{len(value)}_{content_hash}_{suffix}"

    def get_or_create(self, value: str, null_terminated: bool = False) -> ir.GlobalVariable:
        """Get existing or create new string constant with deduplication."""
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
        """Get existing or create new null-terminated string constant."""
        return self.get_or_create(value, null_terminated=True)

    def get_or_create_raw(self, value: str) -> ir.GlobalVariable:
        """Get existing or create new string constant WITHOUT null terminator."""
        return self.get_or_create(value, null_terminated=False)

    def get_string_ptr(self, value: str, null_terminated: bool = False) -> Tuple[ir.GlobalVariable, ir.Value]:
        """Get string constant and a pointer to its first element."""
        global_var = self.get_or_create(value, null_terminated)
        zero = ir.Constant(self.codegen.i32, 0)
        ptr = self.codegen.builder.gep(global_var, [zero, zero], name="str_ptr")
        return global_var, ptr

    def create_string_constant(self, name: str, value: str) -> ir.GlobalVariable:
        """Create a named string constant (uses deduplication)."""
        return self.get_or_create(value, null_terminated=True)

    def clear(self):
        """Clear the string cache."""
        self._cache.clear()

    @property
    def stats(self) -> Dict[str, int]:
        """Return deduplication statistics."""
        return {'unique_strings': len(self._cache)}
