"""Platform-specific random function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_extern
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types

if typing.TYPE_CHECKING:
    pass


def declare_random(module: ir.Module) -> ir.Function:
    """Declare random: long random(void)"""
    _, _, _, i64 = get_basic_types()
    # long is i64 on 64-bit systems, which also matches Sushi's u64 return type
    return declare_extern(module, "random", i64, [])


def declare_srandom(module: ir.Module) -> ir.Function:
    """Declare srandom: void srandom(unsigned int seed)"""
    _, _, i32, _ = get_basic_types()
    return declare_extern(module, "srandom", ir.VoidType(), [i32])


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific random functions."""
    module = ir.Module(name="platform_random")
    module.triple = ""  # Use default target triple

    declare_random(module)
    declare_srandom(module)

    return module
