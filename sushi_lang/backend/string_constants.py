"""String constant management and deduplication."""
from __future__ import annotations
import hashlib
from typing import TYPE_CHECKING, Dict

from llvmlite import ir

from sushi_lang.internals.errors import message_for, raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

PCT_S_FORMAT_NAME = ".runtime_err_pct_s"


def content_digest(text: str) -> str:
    """A STABLE digest of `text`, for a global named after what it holds.

    Python randomizes `hash()` per process, so one library built two times named the
    same literal two ways and its bitcode could not be compared byte for byte, nor
    addressed by its content (#708). Every global whose name carries its text reads
    this one digest.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=4).hexdigest()


def text_global(module: ir.Module, name: str, text: str, nul: bool = True) -> ir.GlobalVariable:
    """The private constant `name` that holds the bytes of `text`, with a NUL after them
    when `nul` is set; one global for each name."""
    data = bytearray(text.encode("utf-8") + (b"\0" if nul else b""))
    existing = module.globals.get(name)
    if isinstance(existing, ir.GlobalVariable):
        if existing.initializer is None or bytes(existing.initializer.constant) != bytes(data):
            raise_internal_error("CE0000", detail=f"the global '{name}' already holds other bytes")
        return existing
    ty = ir.ArrayType(ir.IntType(8), len(data))
    global_str = ir.GlobalVariable(module, ty, name=name)
    global_str.linkage = "private"
    global_str.global_constant = True
    global_str.unnamed_addr = True
    global_str.initializer = ir.Constant(ty, data)
    return global_str


def pct_s_format(module: ir.Module) -> ir.GlobalVariable:
    """The "%s" format that each runtime error gives to fprintf before its text."""
    return text_global(module, PCT_S_FORMAT_NAME, "%s")


def runtime_error_text(module: ir.Module, error_code: str, **params: object) -> ir.GlobalVariable:
    """The line `Runtime Error RExxxx: <message>` of `error_code`, named by its code and its
    content."""
    line = f"Runtime Error {error_code}: {message_for(error_code, **params)}\n"
    digest = hashlib.sha1(line.encode("utf-8")).hexdigest()[:8]
    return text_global(module, f"err_{error_code}_{digest}", line)


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

        global_var = text_global(self.codegen.module,
                                 self._make_global_name(value, null_terminated), value,
                                 nul=null_terminated)
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
