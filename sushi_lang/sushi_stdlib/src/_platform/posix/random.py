"""
Platform-specific random function declarations.

This module declares external random functions from the system's C library.
These declarations work across all POSIX-compliant systems (macOS, Linux, BSD).

Functions:
    - random: Pseudo-random number generator (POSIX standard)
    - srandom: Seed the random number generator

Implementation notes:
    - Uses external linkage (resolved by system linker)
    - Compatible with libSystem (macOS), glibc (Linux), musl (Alpine)
    - No platform-specific code needed - POSIX guarantees compatibility
"""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types

if typing.TYPE_CHECKING:
    pass


def declare_random(module: ir.Module) -> ir.Function:
    """Declare random: long random(void)

    Generates pseudo-random numbers using a non-linear additive feedback
    algorithm. Returns values in the range [0, RAND_MAX] where RAND_MAX
    is guaranteed to be at least 2^31-1.

    POSIX Signature:
        long random(void);

    Returns:
        A value in the range [0, 2^31-1] (31-bit positive values)

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ FreeBSD, OpenBSD, NetBSD
        ✅ Any POSIX.1-2001 compliant system

    Thread Safety:
        ⚠️  NOT thread-safe (uses global state)
        Use random_r() for thread-safe variant (non-portable)
    """
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
    """Declare srandom: void srandom(unsigned int seed)

    Initializes the random number generator using the given seed.
    If srandom() is not called, random() acts as if srandom(1) was called.

    POSIX Signature:
        void srandom(unsigned int seed);

    Parameters:
        seed: Initialization value for the RNG state

    Portability:
        ✅ macOS (via libSystem)
        ✅ Linux (via glibc, musl)
        ✅ FreeBSD, OpenBSD, NetBSD
        ✅ Any POSIX.1-2001 compliant system
    """
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
    """
    Generate LLVM IR module for platform-specific random functions.

    This module only contains external declarations, not implementations.
    The actual implementations are provided by the system's C library.
    """
    module = ir.Module(name="platform_random")
    module.triple = ""  # Use default target triple

    # Declare all platform-specific random functions
    declare_random(module)
    declare_srandom(module)

    return module
