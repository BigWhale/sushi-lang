"""Platform-specific random function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

if typing.TYPE_CHECKING:
    pass


def declare_random(module: ir.Module) -> ir.Function:
    """Declare random: long random(void)"""
    # long is i64 on 64-bit systems, which also matches Sushi's u64 return type
    return declare_libc(module, "random")


def declare_srandom(module: ir.Module) -> ir.Function:
    """Declare srandom: void srandom(unsigned int seed)"""
    return declare_libc(module, "srandom")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific random functions."""
    module = ir.Module(name="platform_random")
    module.triple = ""  # Use default target triple

    declare_random(module)
    declare_srandom(module)

    return module
