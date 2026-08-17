"""Platform-specific random function declarations."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types

if typing.TYPE_CHECKING:
    pass


def declare_random(module: ir.Module) -> ir.Function:
    """Declare random: long random(void)"""
    # Check if already declared
    if "random" in module.globals:
        return module.globals["random"]

    # Get common types
    _, _, _, i64 = get_basic_types()

    # long random(void)
    # Note: long is i64 on 64-bit systems, i32 on 32-bit systems
    # We use i64 for consistency with Sushi's u64 return type
    fn_ty = ir.FunctionType(i64, [])

    # Declare with external linkage (resolved by linker)
    func = ir.Function(module, fn_ty, name="random")

    return func


def declare_srandom(module: ir.Module) -> ir.Function:
    """Declare srandom: void srandom(unsigned int seed)"""
    # Check if already declared
    if "srandom" in module.globals:
        return module.globals["srandom"]

    # Get common types
    _, _, i32, _ = get_basic_types()
    void = ir.VoidType()

    # void srandom(unsigned int seed)
    # We use i32 for unsigned int (32-bit on all platforms)
    fn_ty = ir.FunctionType(void, [i32])

    # Declare with external linkage (resolved by linker)
    func = ir.Function(module, fn_ty, name="srandom")

    return func


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for platform-specific random functions."""
    module = ir.Module(name="platform_random")
    module.triple = ""  # Use default target triple

    # Declare all platform-specific random functions
    declare_random(module)
    declare_srandom(module)

    return module
