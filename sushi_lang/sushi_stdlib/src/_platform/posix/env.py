"""Platform-specific environment variable function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

if typing.TYPE_CHECKING:
    pass


def declare_getenv(module: ir.Module) -> ir.Function:
    """Declare getenv: char* getenv(const char* name)"""
    return declare_libc(module, "getenv")


def declare_setenv(module: ir.Module) -> ir.Function:
    """Declare setenv: int setenv(const char* name, const char* value, int overwrite)"""
    return declare_libc(module, "setenv")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific environment functions."""
    module = ir.Module(name="platform_env")
    module.triple = ""  # Use default target triple

    declare_getenv(module)
    declare_setenv(module)

    return module
