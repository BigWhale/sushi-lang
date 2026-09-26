"""Platform-specific environment variable function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_extern

if typing.TYPE_CHECKING:
    pass


def declare_getenv(module: ir.Module) -> ir.Function:
    """Declare getenv: char* getenv(const char* name)"""
    i8_ptr = ir.IntType(8).as_pointer()
    return declare_extern(module, "getenv", i8_ptr, [i8_ptr])


def declare_setenv(module: ir.Module) -> ir.Function:
    """Declare setenv: int setenv(const char* name, const char* value, int overwrite)"""
    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    return declare_extern(module, "setenv", i32, [i8_ptr, i8_ptr, i32])


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific environment functions."""
    module = ir.Module(name="platform_env")
    module.triple = ""  # Use default target triple

    declare_getenv(module)
    declare_setenv(module)

    return module
