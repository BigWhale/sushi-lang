"""String constant management and deduplication."""
from __future__ import annotations
import hashlib
from typing import TYPE_CHECKING, Dict

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def content_digest(text: str) -> str:
    """A STABLE digest of `text`, for a global named after what it holds.

    Python randomizes `hash()` per process, so one library built two times named the
    same literal two ways and its bitcode could not be compared byte for byte, nor
    addressed by its content (#708). Every global whose name carries its text reads
    this one digest.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=4).hexdigest()


class StringConstantManager:
    """Manages string constants with content-based deduplication."""

    def __init__(self, codegen: 'LLVMCodegen'):
        """Initialize the string constant manager."""
        self.codegen = codegen
        self._cache: Dict[str, ir.GlobalVariable] = {}

    def _make_global_name(self, value: str, null_terminated: bool) -> str:
        """Generate a content-based unique name for a string constant."""
        suffix = "nt" if null_terminated else "raw"
        return f".str.{len(value)}_{content_digest(value)}_{suffix}"

    def get_or_create(self, value: str, null_terminated: bool = False) -> ir.GlobalVariable:
        """Get existing or create new string constant with deduplication."""
        key_str = f"{value}|{'nt' if null_terminated else 'raw'}"

        if key_str in self._cache:
            return self._cache[key_str]

        global_name = self._make_global_name(value, null_terminated)

        existing = self.codegen.module.globals.get(global_name)
        if existing is not None:
            self._cache[key_str] = existing
            return existing

        string_data = bytearray(value.encode('utf-8'))
        if null_terminated:
            string_data.append(0)

        i8 = self.codegen.types.i8
        const_type = ir.ArrayType(i8, len(string_data))
        const_value = ir.Constant(const_type, string_data)

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

    def get_or_create_raw(self, value: str) -> ir.GlobalVariable:
        """Get existing or create new string constant WITHOUT null terminator."""
        return self.get_or_create(value, null_terminated=False)

    def create_string_constant(self, name: str, value: str) -> ir.GlobalVariable:
        """Create a named string constant (uses deduplication)."""
        return self.get_or_create(value, null_terminated=True)

    def clear(self):
        """Clear the string cache."""
        self._cache.clear()
